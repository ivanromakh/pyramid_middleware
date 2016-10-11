from paste.httpserver import serve
from wsgiref.simple_server import make_server
from pyramid.response import Response
from pyramid.view import view_config
from couchdb.http import ResourceConflict
from webob import Request, Response
from io import BytesIO
import socket
import itertools
import traceback

try:
    from couchdb.http import ResourceConflict
except ImportError:
    class ResourceConflict(Exception):
        pass

class Retry:
    def __init__(self, app, tries, retryable=ResourceConflict,  highwater=2<<20,
                 log_after_try_count=1):
        self.tries = tries
        self.app = app
        self.highwater = highwater
        self.log_after_try_count = log_after_try_count

        if retryable is None:
            retryable = (ConflictError, RetryException,)

        if not isinstance(retryable, (list, tuple)):
            retryable = [retryable]
        self.retryable = tuple(retryable)

    def __call__(self, environ, start_response):
        catch_response = []
        written = []
        original_wsgi_input = environ.get('wsgi.input')
        new_wsgi_input = None

        if original_wsgi_input is not None:
            cl = environ.get('CONTENT_LENGTH', '0')
            if cl == '':
                cl = 0
            else:
                cl = int(cl)
            if cl > self.highwater:
                new_wsgi_input = environ['wsgi.input'] = TemporaryFile('w+b')
            else:
                new_wsgi_input = environ['wsgi.input'] = BytesIO()
            rest = cl
            try:
                while rest:
                    if rest <= chunksize:
                        chunk = original_wsgi_input.read(rest)
                        rest = 0
                    else:
                        chunk = original_wsgi_input.read(chunksize)
                        rest = rest - chunksize
                    new_wsgi_input.write(chunk)
            except (socket.error, IOError):
                # Different wsgi servers will generate either socket.error or
                # IOError if there is a problem reading POST data from browser.
                msg = b'Not enough data in request or socket error'
                start_response('400 Bad Request', [
                    ('Content-Type', 'text/plain'),
                    ('Content-Length', str(len(msg))),
                    ]
                )
                return [msg]
            new_wsgi_input.seek(0)

        def replace_start_response(status, headers, exc_info=None):
            catch_response[:] = [status, headers, exc_info]
            return written.append

        i = 0
        while 1:
            app_iter = self.app(environ, replace_start_response)
            print catch_response
            if catch_response[0] == "409 Conflict":
                i += 1
                if i< self.tries:
                    continue
            if catch_response:
                start_response(*catch_response)
            else:
                if hasattr(app_iter, 'close'):
                    app_iter.close()
                raise AssertionError('app must call start_response before '
                                         'returning')
            return close_when_done_generator(written, app_iter)

def close_when_done_generator(written, app_iter):
    try:
        for chunk in itertools.chain(written, app_iter):
            yield chunk
    finally:
        if hasattr(app_iter, 'close'):
            app_iter.close()



@view_config()
def hello(request):
    return Response("blabla", "409 Conflict")

if __name__ == '__main__':
    from pyramid.config import Configurator
    config = Configurator()
    config.add_route('hello', '/hello/{name}')
    config.add_view(hello, route_name='hello')
    config.scan()
    app = config.make_wsgi_app()

    # Put middleware
    app = Retry(app,3, ResourceConflict)
    server = make_server('0.0.0.0', 8080, app)
    server.serve_forever()
