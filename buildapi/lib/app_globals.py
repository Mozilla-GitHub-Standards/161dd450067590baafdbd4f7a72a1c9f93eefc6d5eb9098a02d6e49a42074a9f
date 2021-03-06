"""The application's Globals object"""

from beaker.cache import CacheManager
from beaker.util import parse_cache_config_options

import pytz

from buildapi.lib import cacher, cache

class Globals(object):
    """Globals acts as a container for objects available throughout the
    life of the application

    """

    def __init__(self, config):
        """One instance of Globals is created during application
        initialization and is available during requests via the
        'app_globals' variable

        """
        self.cache = CacheManager(**parse_cache_config_options(config))

        cache_spec = config.get('buildapi.cache')
        tz_name = config.get('timezone')
        tz = pytz.timezone(tz_name)
        self.tz = tz

        self.masters_url = config['masters_url']
        self.branches_url = config['branches_url']

        if hasattr(cacher, 'RedisCache') and cache_spec.startswith('redis:'):
            # TODO: handle other hosts/ports
            bits = cache_spec.split(':')
            kwargs = {}
            if len(bits) >= 2:
                kwargs['host'] = bits[1]

            if len(bits) == 3:
                kwargs['port'] = int(bits[2])
            buildapi_cacher = cacher.RedisCache(**kwargs)
        elif hasattr(cacher, 'MemcacheCache') and cache_spec.startswith('memcached:'):
            hosts = cache_spec[10:].split(',')
            buildapi_cacher = cacher.MemcacheCache(hosts)
        else:
            raise RuntimeError("invalid cache spec %r" % (cache_spec,))

        self.buildapi_cache = cache.BuildapiCache(buildapi_cacher, tz)
