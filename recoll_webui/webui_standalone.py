#!/usr/bin/env python3

def main():
    import os
    import sys
    import argparse
    from importlib import resources
    from recoll_webui import webui
    from recoll_webui import bottle

    # Tell bottle how to look up templates, they are not in the script directory any more.
    with resources.path(webui, 'views') as fspath:
        resourcepath = str(fspath)
    bottle.TEMPLATE_PATH.append(resourcepath)
    staticpath = os.path.join(os.path.dirname(resourcepath), 'static')
    webui.STATIC_DIR = staticpath
    # handle command-line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--addr', default='127.0.0.1',help='address to bind to [127.0.0.1]')
    parser.add_argument('-p', '--port', default='8080', type=int, help='port to listen on [8080]')
    parser.add_argument('-c', '--config', default=None, type=str, help='configuration directory')
    args = parser.parse_args()

    if args.config:
        os.environ["RECOLL_CONFDIR"] = args.config

    # set up webui and run in own http server
    bottle.debug(True)
    bottle.run(server='waitress', host=args.addr, port=args.port)

# vim: foldmethod=marker:filetype=python:textwidth=80:ts=4:et
