# -*- coding: utf-8 -*-

"""
(C) 2014-2019 Roman Sirokov and contributors
Licensed under BSD license

http://github.com/r0x0r/pywebview/
"""

import os
import sys
import logging
import json
import shutil
import tempfile
import webbrowser
from threading import Event, Semaphore
from ctypes import windll

from webview import WebViewException, _debug, _user_agent
from webview.serving import resolve_url
from webview.util import parse_api_js, interop_dll_path, parse_file_type, inject_base_uri, default_html, js_bridge_call
from webview.js import alert
from webview.js.css import disable_text_select

import clr


clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Collections')
clr.AddReference('System.Threading')

import System.Windows.Forms as WinForms
from System import IntPtr, Int32, String, Action, Func, Type, Environment, Uri
from System.Threading.Tasks import Task, TaskScheduler, TaskContinuationOptions
from System.Drawing import Size, Point, Icon, Color, ColorTranslator, SizeF

clr.AddReference(interop_dll_path('Microsoft.Web.WebView2.Core.dll'))
clr.AddReference(interop_dll_path('Microsoft.Web.WebView2.WinForms.dll'))
from Microsoft.Web.WebView2.WinForms import WebView2
from Microsoft.Web.WebView2.Core import CoreWebView2Environment

logger = logging.getLogger('pywebview')

class EdgeChrome:
    def __init__(self, form, window):
        self.pywebview_window = window
        self.web_view = WebView2()

        form.Controls.Add(self.web_view)

        self.js_result_semaphore = Semaphore(0)
        self.web_view.Dock = WinForms.DockStyle.Fill
        #settings under on_webview_ready 
        self.web_view.CoreWebView2Ready += self.on_webview_ready
        self.web_view.NavigationStarting += self.on_navigation_start
        self.web_view.NavigationCompleted += self.on_navigation_completed
        self.web_view.WebMessageReceived += self.on_script_notify

        self.httpd = None # HTTP server for load_html
        self.tmpdir = None
        self.url = None
        self.ishtml = False

        if window.html or 'localhost' in window.real_url or '127.0.0.1' in window.real_url:
            _allow_localhost()

        if window.real_url:
            self.load_url(window.real_url)
        elif window.html:
            self.load_html(window.html, '')
        else:
            self.load_html(default_html, '')

    def evaluate_js(self, script):
        def callback(result):
            self.js_result = None if result is None or result == '' else json.loads(result)
            self.js_result_semaphore.release()

        self.syncContextTaskScheduler = TaskScheduler.FromCurrentSynchronizationContext()
        try:
            result = self.web_view.ExecuteScriptAsync(script).ContinueWith(
            Action[Task[String]](
                lambda task: callback(json.loads(task.Result))
            ),
            self.syncContextTaskScheduler)
        except Exception as e:
            logger.exception('Error occurred in script')
            self.js_result = None
            self.js_result_semaphore.release()

    def get_current_url(self):
        return self.url

    def load_html(self, html, base_uri):
        self.tmpdir = tempfile.mkdtemp()
        self.temp_html = os.path.join(self.tmpdir, 'index.html')

        with open(self.temp_html, 'w', encoding='utf-8') as f:
            f.write(inject_base_uri(html, base_uri))

        if self.httpd:
            self.httpd.shutdown()

        url = resolve_url('file://' + self.temp_html, True)
        self.ishtml = True
        self.web_view.Source = Uri(url)

    def load_url(self, url):
        self.ishtml = False
        self.web_view.Source = Uri(url)

    def on_script_notify(self, _, args):
        try:
            func_name, func_param, value_id = json.loads(args.get_WebMessageAsJson())

            if func_name == 'alert':
                WinForms.MessageBox.Show(func_param)
            elif func_name == 'console':
                print(func_param)
            else:
                js_bridge_call(self.pywebview_window, func_name, func_param, value_id)
        except Exception as e:
            logger.exception('Exception occured during on_script_notify')

    def on_new_window_request(self, _, args):
        args.set_Handled(True)
        #webbrowser.open(str(args.get_Uri()))

    def on_webview_ready(self, sender, args):
        sender.CoreWebView2.NewWindowRequested += self.on_new_window_request
        settings = sender.CoreWebView2.Settings
        settings.AreDefaultContextMenusEnabled = True
        settings.AreDefaultScriptDialogsEnabled = True
        settings.AreDevToolsEnabled = True
        settings.IsBuiltInErrorPageEnabled = True 
        settings.IsScriptEnabled = True
        settings.IsWebMessageEnabled = True
        settings.IsStatusBarEnabled = True
        settings.IsZoomControlEnabled = True
        
    def on_navigation_start(self, sender, args):
        pass

    def on_navigation_completed(self, sender, args):
        try:
            if self.tmpdir and os.path.exists(self.tmpdir):
                shutil.rmtree(self.tmpdir)
                self.tmpdir = None
        except Exception as e:
            logger.exception('Failed deleting %s' % self.tmpdir)

        url = str(sender.Source)
        self.url = None if self.ishtml else url
        self.web_view.ExecuteScriptAsync('window.alert = (msg) => window.chrome.webview.postMessage(["alert", msg+"", ""])')

        if _debug:
            self.web_view.ExecuteScriptAsync('window.console = { log: (msg) => window.chrome.webview.postMessage(["console", msg+"", ""])}')

        self.web_view.ExecuteScriptAsync(parse_api_js(self.pywebview_window, 'chromium'))

        if not self.pywebview_window.text_select:
            self.web_view.ExecuteScriptAsync(disable_text_select)

        self.pywebview_window.loaded.set()

def _allow_localhost():
    import subprocess

    # lifted from https://github.com/pyinstaller/pyinstaller/wiki/Recipe-subprocess
    def subprocess_args(include_stdout=True):
        if hasattr(subprocess, 'STARTUPINFO'):
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            env = os.environ
        else:
            si = None
            env = None

        if include_stdout:
            ret = {'stdout': subprocess.PIPE}
        else:
            ret = {}

        ret.update({'stdin': subprocess.PIPE,
                    'stderr': subprocess.PIPE,
                    'startupinfo': si,
                    'env': env })
        return ret

    output = subprocess.check_output('checknetisolation LoopbackExempt -s', **subprocess_args(False))
        
