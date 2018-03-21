# collectd-write-warp10
Python module for collectd to push metrics in the warp format (http request)
  
This module is interpreted by the Python module from Collectd v5.7.
  
  
## Features

Multi-endpoints  
You can push metrics on differents endpoints.  
  
Queue  
In case of network troubles, points are kept for a moment  
  
Default labels  
You can add default labels to your metrics, like hostname of the machine  
  
RewriteRules  
You can format your metrics as you wish with regexs. See bellow ...
  
  
## Configuration
  
Write_warp10 module has to be imported with the Python module.  
To do that, add a config file like '10-python.conf' in the collectd config directory to load the python plugin:  
Config in 10-python.conf:  
```
<LoadPlugin python>
  Globals true
</LoadPlugin>
```
  
Then add a config file 'python-config.conf' with your config:  
Simple config example:
```
<Plugin "python">

  Import "write_warp10"

  <Module "write_warp10">
    DefaultLabel hostname "server-42.example.com"
    DefaultLabel infra "1"
    Token "warp10_write_token"
    URL "https://warp.example.com/api/v0/update"
  </Module>

</Plugin>
```
  
  
## RewriteRules
  
Complex config example with RewritesRules:
```
<Plugin "python">

  Import "write_warp10"

  <Module "write_warp10">
    DefaultLabel hostname "server-42.example.com"
    DefaultLabel infra "1"
    RewriteRule "^(.*)\\.(value|counter|derive|gauge|absolute)$" "\\1" "N"
    RewriteRule "^(.*\\.)sda3(\\..*)$" "\\1disk-00-012\\2"
    RewriteRule "^disk\\.(disk-[0-9][0-9]-[0-9][0-9][0-9])\\.(disk_|)(.*)$" "disk.\\3" "T:disk=\\1,L"
    RewriteRule "^disk\\.(.*)$" "null" "F"
    RewriteRule "^df\\.(disk-[0-9][0-9]-[0-9][0-9][0-9])\\.(df_|)(.*)$" "fs.\\3" "T:disk=\\1,L"
    RewriteRule "^df\\.(.*)$" "null" "F"
    Token "warp10_write_token"
    URL "https://warp.example.com/api/v0/update"
  </Module>

</Plugin>
```
  
In this example, we have some 'RewriteRules'.  
There are differents usages, specified with letters:  
* N for Next
* T for Tag (which means labels)
* L for Last
* F for Forbidden (or Forget)  
  
These letters come from apache http server project.
  
```
RewriteRule "^(.*)\\.(value|counter|derive|gauge|absolute)$" "\\1" "N"
```
This rule contains a N, that's mean the regex will be applied again if it matchs.  
So if we have a point for a metric named "test.value.counter", after the first iteration it will become "test.value" and, because of the N (for Next), it will be applied again an become "test".  
  
```
RewriteRule "^(.*\\.)sda3(\\..*)$" "\\1disk-00-012\\2"
```
This rule is a simple replacement, replacing 'sda3' by 'disk-00-012' in the metric name.  
  
```
RewriteRule "^disk\\.(disk-[0-9][0-9]-[0-9][0-9][0-9])\\.(disk_|)(.*)$" "disk.\\3" "T:disk=\\1,L"
```
In the previous rule, the metric name has been renamed with 'disk-00-012'.  
Regexs are called in the order of the declaration, so this regex match the metric and format it.  
It remove the part 'disk_*' if exists, and it move the disk name (disk-00-012) in the label 'disk' (with the letter T).  
The letter L is added to be sure that the metric (which is perfect now) will not be formated with another regex bellow.  
  
```
RewriteRule "^disk\\.(.*)$" "null" "F"
```
This rule is just here to delete useless metrics. All metrics matching the regex will be delete (F for forbidden). That's why the previous rule have a L (for Last).  
Next rules use the same method.  
  
  
## Collectd v5.4 compatibility
To use the module in collectd 5.4, change the collectd_dataset like this:  
  
Add this:  
+# collectd_dataset is here to compensate lack of collectd.get_dataset method  
+import collectd_datasets  
  
And change this:  
-datasets = collectd.get_dataset(vl.type)  
by this:  
+# this is to compensate lack of availability of get_dataset method in  
+# collectd 5.4, collectd_datasets.get_dataset is a drop-in replacment  
+# for collectd.get_dataset  
+datasets = collectd_datasets.get_dataset(vl.type)
