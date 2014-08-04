import pkgutil
import mimetypes
import time

from datetime import datetime

from pywb.utils.wbexception import NotFoundException
from pywb.utils.loaders import BlockLoader

from pywb.framework.basehandlers import BaseHandler, WbUrlHandler
from pywb.framework.wbrequestresponse import WbResponse

from pywb.warc.recordloader import ArcWarcRecordLoader
from pywb.warc.resolvingloader import ResolvingLoader

from views import J2TemplateView
from replay_views import ReplayView
from pywb.utils.timeutils import datetime_to_timestamp


#=================================================================
class SearchPageWbUrlHandler(WbUrlHandler):
    """
    Loads a default search page html template to be shown when
    the wb_url is empty
    """
    def __init__(self, config):
        self.search_view = (J2TemplateView.
                            create_template(config.get('search_html'),
                           'Search Page'))

        self.is_frame_mode = config.get('framed_replay', False)

        if self.is_frame_mode:
            html = config.get('frame_insert_html', 'ui/frame_insert.html')
            self.frame_insert_view = (J2TemplateView.
                                      create_template(html, 'Frame Insert'))
        else:
            self.frame_insert_view = None

    def render_search_page(self, wbrequest, **kwargs):
        if self.search_view:
            return self.search_view.render_response(wbrequest=wbrequest,
                                                    prefix=wbrequest.wb_prefix,
                                                    **kwargs)
        else:
            return WbResponse.text_response('No Lookup Url Specified')

    def __call__(self, wbrequest):
        # root search page
        if wbrequest.wb_url_str == '/':
            return self.render_search_page(wbrequest)

        # render top level frame if in frame mode
        # (not supported in proxy mode)
        if (self.is_frame_mode and wbrequest.wb_url and
            not wbrequest.wb_url.is_query() and
            not wbrequest.wb_url.mod and
            not wbrequest.options['is_proxy']):

            params = self.get_top_frame_params(wbrequest)

            return self.frame_insert_view.render_response(**params)

        return self.handle_request(wbrequest)

    def get_top_frame_params(self, wbrequest):
        if wbrequest.wb_url.timestamp:
            timestamp = wbrequest.wb_url.timestamp
        else:
            timestamp = datetime_to_timestamp(datetime.utcnow())

        embed_url = wbrequest.wb_url.to_str(mod='mp_')

        return dict(embed_url=embed_url,
                    wbrequest=wbrequest,
                    timestamp=timestamp,
                    url=wbrequest.wb_url.url,
                    content_type='text/html')


#=================================================================
# Standard WB Handler
#=================================================================
class WBHandler(SearchPageWbUrlHandler):
    def __init__(self, query_handler, config=None):
        super(WBHandler, self).__init__(config)

        self.index_reader = query_handler

        cookie_maker = config.get('cookie_maker')
        record_loader = ArcWarcRecordLoader(cookie_maker=cookie_maker)

        paths = config.get('archive_paths')

        resolving_loader = ResolvingLoader(paths=paths,
                                           record_loader=record_loader)

        self.replay = ReplayView(resolving_loader, config)

        self.fallback_handler = None
        self.fallback_name = config.get('fallback')

    def resolve_refs(self, handler_dict):
        if self.fallback_name:
            self.fallback_handler = handler_dict.get(self.fallback_name)

    def handle_request(self, wbrequest):
        try:
            response = self.handle_query(wbrequest)
        except NotFoundException as nfe:
            return self.handle_not_found(wbrequest, nfe)

        if isinstance(response, WbResponse):
            return response

        cdx_lines, cdx_callback = response
        return self.handle_replay(wbrequest, cdx_lines, cdx_callback)

    def handle_query(self, wbrequest):
        return self.index_reader.load_for_request(wbrequest)

    def handle_replay(self, wbrequest, cdx_lines, cdx_callback):
        return self.replay.render_content(wbrequest,
                                          cdx_lines,
                                          cdx_callback)

    def handle_not_found(self, wbrequest, nfe):
        if (not self.fallback_handler or
            wbrequest.wb_url.is_query() or
            wbrequest.wb_url.is_identity):
            raise

        return self.fallback_handler(wbrequest)

    def __str__(self):
        return 'Web Archive Replay Handler'


#=================================================================
# Static Content Handler
#=================================================================
class StaticHandler(BaseHandler):
    def __init__(self, static_path):
        mimetypes.init()

        self.static_path = static_path
        self.block_loader = BlockLoader()

    def __call__(self, wbrequest):
        full_path = self.static_path + wbrequest.wb_url_str

        try:
            data = self.block_loader.load(full_path)

            try:
                data.seek(0, 2)
                size = data.tell()
                data.seek(0)
                headers = [('Content-Length', str(size))]
            except IOError:
                headers = None

            if 'wsgi.file_wrapper' in wbrequest.env:
                reader = wbrequest.env['wsgi.file_wrapper'](data)
            else:
                reader = iter(lambda: data.read(), '')

            content_type, _ = mimetypes.guess_type(full_path)

            return WbResponse.text_stream(data,
                                          content_type=content_type,
                                          headers=headers)

        except IOError:
            raise NotFoundException('Static File Not Found: ' +
                                    wbrequest.wb_url_str)

    def __str__(self):  # pragma: no cover
        return 'Static files from ' + self.static_path


#=================================================================
# Debug Handlers
#=================================================================
class DebugEchoEnvHandler(BaseHandler):  # pragma: no cover
    def __call__(self, wbrequest):
        return WbResponse.text_response(str(wbrequest.env))


#=================================================================
class DebugEchoHandler(BaseHandler):  # pragma: no cover
    def __call__(self, wbrequest):
        return WbResponse.text_response(str(wbrequest))
