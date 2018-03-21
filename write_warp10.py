import collectd
import hashlib
import math
import re
import sys, traceback
import urllib
import urllib2

from Queue import Queue, Empty, Full
from threading import Timer


class WriteWarp10(object):
    def __init__(self, url, token, flush_interval, flush_retry_interval,
                 buffer_size, default_labels, rewrite_rules, rewrite_limit):
        self.url = url
        self.token = token
        self.flush_interval = flush_interval
        self.flush_retry_interval = flush_retry_interval
        self.buffer_size = buffer_size
        self.default_labels = default_labels
        self.rewrite_rules = rewrite_rules
        self.rewrite_limit = rewrite_limit

        self.queue = Queue(buffer_size)
        self.flush_timer = None

    @staticmethod
    def config(cfg):
        # Handle legacy config (not multiple-endpoint capable)
        if not any([n.key == 'Endpoint' for n in cfg.children]):
            # Create fake intermediary Endpoint node
            cfg.children = (collectd.Config('Endpoint', cfg, ('default', ),
                                            cfg.children), )

        endpoints = []
        for node in cfg.children:
            if node.key == 'Endpoint':
                endpoint = WriteWarp10.config_endpoint(node)
                if endpoint:
                    if any(e['name'] == endpoint['name'] for e in endpoints):
                        collectd.warning('write_warp10 plugin: Duplicate '
                                         'endpoint: %s' % endpoint['name'])
                    else:
                        endpoints.append(endpoint)
            else:
                collectd.warning('write_warp10 plugin: Unknown config key: '
                                 '%s' % node.key)

        if endpoints:
            for e in endpoints:
                ww10 = WriteWarp10(e['url'], e['token'], e['flush_interval'],
                                   e['flush_retry_interval'], e['buffer_size'],
                                   e['default_labels'],
                                   e['rewrite_rules'], e['rewrite_limit'])
                collectd.info('write_warp10 plugin: register init write and '
                              'shutdown functions')
                collectd.register_init(ww10.init,
                                       name='write_warp10/%s' % e['name'])
                collectd.register_write(ww10.write,
                                        name='write_warp10/%s' % e['name'])
                collectd.register_shutdown(ww10.shutdown,
                                           name='write_warp10/%s' % e['name'])
        else:
            collectd.warning('write_warp10 plugin: No valid endpoints found')

    @staticmethod
    def config_endpoint(cfg):
        endpoint = {'name': None,
                    'url': None,
                    'token': None,
                    'flush_interval': 30.0,
                    'flush_retry_interval': 10.0,
                    'buffer_size': 65536,
                    'default_labels': {},
                    'rewrite_rules': [],
                    'rewrite_limit': 10}
        if len(cfg.values) == 1:
            endpoint['name'] = cfg.values[0]
        for node in cfg.children:
            if node.key == 'URL':
                endpoint['url'] = node.values[0]
            elif node.key == 'Token':
                endpoint['token'] = node.values[0]
            elif node.key == 'FlushInterval':
                endpoint['flush_interval'] = float(node.values[0])
            elif node.key == 'FlushRetryInterval':
                endpoint['flush_retry_interval'] = float(node.values[0])
            elif node.key == 'BufferSize':
                endpoint['buffer_size'] = int(node.values[0])
            elif node.key == 'DefaultLabel':
                endpoint['default_labels'][node.values[0]] = node.values[1]
            elif node.key == 'RewriteLimit':
                endpoint['rewrite_limit'] = int(node.values[0])
            elif node.key == 'RewriteRule':
                if len(node.values) not in [2,3]:
                    collectd.warning('write_warp10 plugin: Invalid '
                                     'RewriteRule declaration: '
                                     '%s' % node.values)
                    continue
                rule = re.compile(r'%s' % node.values[0])
                rewrite = r'%s' % node.values[1]
                flags = []
                if len(node.values) == 3:
                    flags = [r'%s' % f.strip()
                             for f in node.values[2].split(',') if f.strip()]
                endpoint['rewrite_rules'].append([rule, rewrite, flags])
            else:
                collectd.warning('write_warp10 plugin: Unknown config key for '
                                 'Endpoint: %s' % node.key)
        if not endpoint['name'] or not endpoint['url'] \
                or not endpoint['token']:
            collectd.warning('write_warp10 plugin: Missing name, URL or Token '
                             'config for Endpoint')
            endpoint = None

        return endpoint

    def init(self):
        self.flush_timer = Timer(self.flush_interval, self._flush_timer)
        self.flush_timer.start()

    def write(self, vl, data=None):
        datasets = collectd.get_dataset(vl.type)
        for ds, value in zip(datasets, vl.values):
            if math.isnan(value):
                continue
            ds_name, ds_type, ds_min, ds_max = ds
            classname, new_labels = self._format(vl.plugin, vl.plugin_instance,
                                                 vl.type, vl.type_instance,
                                                 ds_name, ds_type)
            if classname is None:
                # Ignore classname that are unset (it's a feature from rewrite
                # rule to destroy a point)
                continue

            labels = self.default_labels.copy()
            labels.update(vl.meta)
            labels.update(new_labels)
            # Remove empty values
            labels = {k: str(v).strip() for k, v in labels.items()
                      if v is not None and str(v).strip()}

            msg = '%d// %s{%s} %f' % (
                int(1000000*vl.time),  # Microseconds
                classname,
                urllib.urlencode(labels).replace('&', ', '),
                value)

            try:
                self.queue.put_nowait(msg)
            except Full:
                collectd.warning('write_warp10 plugin: Buffer is full (%s '
                                 'elements) for endpoint "%s". The WARP '
                                 'endpoint may encounter issues. Otherwise, '
                                 'consider increasing BufferSize or reducing '
                                 'FlushInterval' % (self.queue.qsize(),
                                                    self.url))

    def _format(self, *arr):
        classname = urllib.quote('.'.join([x.strip()
                                           for x in arr if x.strip()]))
        labels = {}

        for _ in xrange(self.rewrite_limit):
            last = False
            next_round = False
            for rule, rewrite, flags in self.rewrite_rules:
                last = False
                next_round = False
                matches = re.match(rule, classname)
                if matches:
                    # Replacement
                    classname = re.sub(rule, rewrite, classname)

                    # Apply flags
                    for flag in flags:
                        if flag == 'F':
                            return None, None
                        elif flag == 'L':
                            last = True
                        elif flag == 'N':
                            next_round = True
                        elif flag.startswith('T:'):
                            lbl_name, lbl_value = flag[2:].split('=', 1)
                            for ma in re.findall(r'(\\[0-9]+)', lbl_name):
                                v = matches.group(int(ma[1:]))
                                lbl_name = lbl_name.replace(ma, v)
                            for ma in re.findall(r'(\\[0-9]+)', lbl_value):
                                v = matches.group(int(ma[1:]))
                                lbl_value = lbl_value.replace(ma, v)
                            labels[lbl_name] = lbl_value
                if last or next_round:
                    break
            else:
                last = True  # Implicit last if we reach end of rules

            if last and next_round:
                raise Exception('write_warp10 plugin: Incompatible rewrite '
                                'flags in the same rule: L and N')
            elif last:
                break
            elif next_round:
                pass
        else:
            raise Exception('write_warp10 plugin: Rewrite limit exceeded')

        return classname, labels

    def shutdown(self):
        collectd.info("write_warp10 plugin: Shutdown: Start")
        self.flush_timer.cancel()
        collectd.info("write_warp10 plugin: Shutdown: Timer cancelled")
        self.flush_timer.join()
        collectd.info("write_warp10 plugin: Shutdown: Timer thread joined")
        try:
            self._flush()
        except Exception as e:
            stack_str = repr(traceback.format_exception(*sys.exc_info()))
            collectd.error('write_warp10 plugin: Failed to post data before '
                           'shutdown: %s' % stack_str)

    def _flush_timer(self):
        try:
            self._flush()
            next_interval = self.flush_interval
        except:
            next_interval = self.flush_retry_interval

        self.flush_timer = Timer(next_interval, self._flush_timer)
        self.flush_timer.daemon = True
        self.flush_timer.start()

    def _flush(self):
        messages = []
        try:
            while True:
                messages.append(self.queue.get_nowait())
        except Empty:
            pass

        if len(messages) > 0:
            for msg in messages:
                collectd.debug('write_warp10 plugin: Posting: %s' % msg)
            try:
                # Header X-CityzenData-Token is deprecated in favor of
                # X-Warp10-Token, keeping compatibility
                headers = {'X-Warp10-Token': self.token,
                           'X-CityzenData-Token': self.token}
                body = "\n".join(messages)
                req = urllib2.Request(self.url, body, headers)
                resp = urllib2.urlopen(req, timeout=80)
                if resp.getcode() != 200:
                    raise Exception('%d %s' % (resp.getcode(),
                                               resp.read()))
            except Exception as e:
                stack_str = repr(traceback.format_exception(*sys.exc_info()))
                collectd.error('write_warp10 plugin: Failed to post data: '
                               '%s' % stack_str)

                try:
                    for msg in messages:
                        self.queue.put_nowait(msg)
                except Full:
                    collectd.warning('write_warp10 plugin: Buffer is full (%s '
                                     'elements) for endpoint "%s". The WARP '
                                     'endpoint may encounter issues. '
                                     'Otherwise, consider increasing '
                                     'BufferSize or reducing FlushInterval' % (
                                         self.queue.qsize(), self.url))
                raise


collectd.register_config(WriteWarp10.config)

