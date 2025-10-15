#!/usr/bin/python3

import os
from importlib import resources

#
# Possibly adjust the PATH (e.g. add /usr/local/bin on bsd)
#os.environ['PATH'] = os.environ['PATH'] + ':' + '/usr/local/bin'

#
# Possibly designate the recoll configuration directory, for the case
# where tilde expansion does not work in the web server context. Make
# sure that the location and files are readable by the web server user
#os.environ['RECOLL_CONFDIR'] = '/path/to/recoll/configdir'

try:
    from recoll_webui import webui
    from recoll_webui import bottle
except:
    import webui
    import bottle
        
# Tell bottle how to look up templates, they are not in the script directory any more.
with resources.path(webui, 'views') as fspath:
    resourcepath = str(fspath)
bottle.TEMPLATE_PATH = [resourcepath,]
staticpath = os.path.join(os.path.dirname(resourcepath), 'static')
webui.STATIC_DIR = staticpath

application = bottle.default_app()
