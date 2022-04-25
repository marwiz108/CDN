import csv, argparse, sys, zlib, logging

from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

import utils

'''  Set the logging parameters'''
logging.basicConfig(filename='http_server.log', format='%(asctime)s %(levelname)s:%(message)s', level=logging.DEBUG)


''' Constant set of fields to use in HTTP requests '''
CACHE_LIMIT: int = 20000000      # Cache size/CDN server limit: 20MB
ORIGIN_PORT: int = 8080
CONTENT_TYPE_HEADER: str = 'Content-type'
CONTENT_TYPE: str = 'text/html'

# MAX Message Buffer length
BUFFER_SIZE: int = 4096

# Message encode and decode format
FORMAT: str = 'utf-8'

# Set the CDN and Origin server port and hostname, repectively
global http_port
global origin_hostname
http_port: int = 8080
origin_hostname: str = 'cs5700cdnorigin.ccs.neu.edu'

class CacheManager:
    def __init__(self, origin_addr='', origin_port=0):
        self.origin_addr = origin_addr
        self.origin_port = origin_port

        # The CDN server cache
        self.CACHE: dict = {}

        # Load the popularity data in the cache directory
        self.load_popularity_data()

    def load_popularity_data(self) -> None:
        '''
            Function: load_popularity_data() - this method is responsible for loading the cache dictionary with the HTML data of the most popular Wikipedia queries.
                1. reads the wiki queries from the pageviews dump CSV file and sends a GET request for the queries to the origin server. 
                2. Origin server response is stored in the cache for the corresponding search query. 
                3. cache is filled until it reaches thes storage limit of 20MB.
                4. compression and decompression method is implemented to increase the amount of cached responses.
            Parameters: none
            Returns: none
        '''
        # Open CSV file
        with open('pageviews_dump.csv', 'r') as csv_file:
            # Instantiate the CSV reader
            csv_reader = csv.reader(csv_file, quotechar='"', delimiter=',')
            # Read the line items
            for line_item in csv_reader:
                # Perform a check whether the cache size has reached the limit. If not, continue filling.
                if (self.get_cache_size(self.CACHE) < CACHE_LIMIT):
                    # Download data from the origin server and save it in the cache in order of the popularity hits
                    query = line_item[0]
                    # Build the origin server GET request url
                    origin_request_url = utils.build_request_URL(origin_hostname, ORIGIN_PORT, query)
                    # Send GET request to the origin server and receive response
                    response = requests.get(origin_request_url)
                    
                    # Check for successful HTTP response code. Only cache the queries for which no server error has been received.
                    if (response.status_code == 200):
                        # Compress the origin server's response
                        compressed_response = zlib.compress(response.content)

                        # Check if adding the response to the cache overloads the memory or not during runtime
                        if (self.get_cache_size(self.CACHE) + len(compressed_response) > CACHE_LIMIT):
                            break

                        # Cache the compressed origin server response
                        self.CACHE[query] = compressed_response

    def get_cache_size(self, obj, seen=None) -> int:
        ''' Referred from: https://gist.github.com/bosswissam/a369b7a31d9dcab46b4a034be7d263b2#file-pysize-py '''
        '''
            Function: get_cache_size() - this method is responsible for recursively finding the size of cache objects in memory (in bytes)
            Parameters:
                1. obj: The cache dictionary
                2. seen: Visited cache item
            Returns: The size of the cache dictionary in memory
        '''
        size = sys.getsizeof(obj)
        if seen is None:
            seen = set()
        obj_id = id(obj)
        if obj_id in seen:
            return 0

        # Important mark as seen *before* entering recursion to gracefully handle
        # self-referential objects
        seen.add(obj_id)
        if isinstance(obj, dict):
            size += sum([self.get_cache_size(v, seen) for v in obj.values()])
            size += sum([self.get_cache_size(k, seen) for k in obj.keys()])
        elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes, bytearray)):
            size += sum([self.get_cache_size(i, seen) for i in obj])
            if hasattr(obj, '__dict__'):
                size += self.get_cache_size(obj.__dict__.values(), seen)
        elif hasattr(obj, '__dict__'):
            size += self.get_cache_size(obj.__dict__, seen)
        
        return size


class CDNHTTPRequestHandler(BaseHTTPRequestHandler):
    # Initialize the Wiki query cache
    global cm
    cm = CacheManager()
    
    def do_GET(self) -> None:
        '''
            Function: do_GET() - this method is responsible for the GET request management of the HTTP server. The method also manages cache vs. origin server response and error and special HTTP response code handling. It provides the following function:
                1. IF (query_path = "/grading/beacon")  [Special Case]
                    reply with HTTP code - 204 (empty response)
                2. IF (query_path is INVALID)
                    reply with error response - 400 (bad request)
                
                3. IF (query_path in CACHE)
                    reply with the cached response reducing response time - 200
                4. IF (query_path not in CACHE)
                    a. retrieve response from the Origin Server by sending a GET request
                    b. parse the response and the corresponding code
                    c. send appropriate response back to the client
            Parameters: none
            Returns: none
        '''
        # Check if current path url in cache
        try:
            # Validate the path (Special Case)
            if (self.path == '/grading/beacon'):
                logging.debug(f'Response code: NO CONTENT(204)')
                # Build the HTTP response headers
                self.send_response(204)
                self.send_header(CONTENT_TYPE_HEADER, CONTENT_TYPE)
                self.end_headers()
                # Send the empty response to the client
                self.wfile.write(f'Response code: NO CONTENT(204)'.encode())   # TODO: Send something
            
            else:
                if (len(self.path.split('/')) > 2):
                    logging.debug(f'Response code: BAD REQUEST(400)')
                    self.send_error(400, 'Bad request')    # Bad Request

                # Parse the client search query
                query = self.path.split('/')[-1]

                global cm
                if query in cm.CACHE.keys():
                    # Extract the cached response
                    cached_response = cm.CACHE.get(query)   # Compressed response
                    response = zlib.decompress(cached_response)    # Decompress response

                    # Build the HTTP response headers
                    self.send_response(200)
                    self.send_header(CONTENT_TYPE_HEADER, CONTENT_TYPE)
                    self.end_headers()
                    # log response
                    response_size = sys.getsizeof(response) / (1000 * 1000)
                    logging.debug(f'Response Size: {response_size}')
                    # Send the cached response to the client
                    self.wfile.write(response)

                # Current path url not in cache
                else:
                    # Build the origin server GET request url
                    global args
                    origin_request_url = utils.build_request_URL(origin_hostname, 8080, query)
                    # Send GET request to the origin server and receive response
                    response = requests.get(origin_request_url)
                    
                    # Check for content not found
                    if (response.status_code != 200):
                        logging.debug(f'Response code: NOT FOUND({response.status_code})')
                        self.send_error(404, 'Not Found')    # Not found
                    
                    else:
                        # Decode the origin server response
                        origin_response = response.content  # text/HTML

                        # Build the HTTP response headers
                        self.send_response(200)
                        self.send_header(CONTENT_TYPE_HEADER, CONTENT_TYPE)
                        self.end_headers()
                        # Send the origin response to the client
                        logging.debug(f'Response code: NOT FOUND({response.status_code})')
                        self.wfile.write(origin_response)

        except requests.exceptions.RequestException as error:
            raise(error)


def start_CDN_server() -> None:
    '''
        Function: start_CDN_server() - this method is responsible for firstly, retrieving the CDN IP address, and
            starting the server on that IP address and the CDN server port number. Lastly, the server is started and
            is controlled by the [deploy|run|stop]CDN scripts.
        Parameters: none
        Returns: none
    '''
    # Extract the CDN host IP addresss
    my_IP = utils.get_my_ip()
    # Instantiate the HTTP server, with the CDN IP address and port number 
    # and the HTTP request handler class managing the GET requests
    http_server = HTTPServer((my_IP, http_port), CDNHTTPRequestHandler)
    # Run the HTTP server
    http_server.serve_forever()


if __name__ == "__main__":
    ''' Script argument parser '''
    parser = argparse.ArgumentParser(description='HTTP Server')

    # Store HTTP port no. and Origin server hostname from terminal
    parser.add_argument('-p', dest='http_port', type=int, action='store', help='<CDN Server Port>')
    parser.add_argument('-o', dest='origin_hostname', type=str, action='store', help='<Origin Hostname>')
    args = parser.parse_args()

    # Extract the input HTTP port and Origin Server hostname
    http_port = args.http_port
    origin_hostname = args.origin_hostname
    
    # Start the CDN HTTP Server
    start_CDN_server()