import collections
import traceback

from .logger import get_logger

logger = get_logger(__name__)


class Listenable(object):
    def __init__(self):
        self.__listeners = collections.OrderedDict()

    def add_listener(self, event, callback):
        callbacks = self.__listeners.get(event)
        if callbacks == None:
            callbacks = []
            self.__listeners[event] = callbacks
        callbacks.append(callback)

    def delete_listener(self, event, callback):
        callbacks = self.__listeners.get(event)
        if callbacks == None:
            return
        try:
            callbacks.remove(callback)
        except Exception:
            pass

    def notify(self, event, *argv, **kwargs):
        callbacks = self.__listeners.get(event, [])
        for callback in callbacks:
            try:
                callback(*argv, **kwargs)
            except Exception:
                logger.error("Failed to notify: %s", traceback.format_exc())
