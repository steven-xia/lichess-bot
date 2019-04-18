import multiprocessing
import traceback
from multiprocessing.pool import Pool


# Shortcut to multiprocessing's logger
def error(msg, *args):
    return multiprocessing.get_logger().error(msg, *args)


class LogExceptions(object):
    def __init__(self, callable_func):
        self.__callable = callable_func

    def __call__(self, *args, **kwargs):
        try:
            result = self.__callable(*args, **kwargs)

        except Exception as err:
            # Here we add some debugging help. If multiprocessing's
            # debugging is on, it will arrange to log the traceback
            error(traceback.format_exc())
            # Re-raise the original exception so the Pool worker can
            # clean up
            raise

        # It was fine, give a normal answer
        return result


class LoggingPool(Pool):
    def apply_async(self, func, args=(), kwargs={}, callback=None):
        return Pool.apply_async(self, LogExceptions(func), args, kwargs, callback)
