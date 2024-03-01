import asyncio
import enum
import websockets
from abc import ABC, abstractmethod
from typing import Callable
import maxwell.protocol.maxwell_protocol_pb2 as protocol_types
import maxwell.protocol.maxwell_protocol as protocol
from .listenable import Listenable
from .logger import get_logger

logger = get_logger(__name__)


class Event(enum.Enum):
    ON_CONNECTING = 100
    ON_CONNECTED = 101
    ON_DISCONNECTING = 102
    ON_DISCONNECTED = 103
    ON_ERROR = 104


class EventHandler:
    def on_connecting(*argv, **kwargs):
        pass

    def on_connected(*argv, **kwargs):
        pass

    def on_disconnecting(*argv, **kwargs):
        pass

    def on_disconnected(*argv, **kwargs):
        pass

    def on_error(*argv, **kwargs):
        pass


class ErrorCode(enum.Enum):
    OK = 0
    FAILED_TO_ENCODE = 1
    FAILED_TO_SEND = 2
    FAILED_TO_DECODE = 3
    FAILED_TO_RECEIVE = 4
    FAILED_TO_CONNECT = 5
    FAILED_TO_DISCONNECT = 6
    UNKNOWN_ERROR = 99


class Error(Exception):
    def __init__(self, code, desc):
        self.code = code
        self.desc = desc

    def __str__(self):
        return f"maxwell.utils.connection.Error: code: {self.code}, desc: {self.desc}"


class AbstractConnection(ABC, Listenable):
    @abstractmethod
    def endpoint(self):
        raise NotImplementedError

    @abstractmethod
    async def wait_open(self):
        raise NotImplementedError

    @abstractmethod
    def is_open(self):
        raise NotImplementedError

    @abstractmethod
    async def request(self, msg):
        raise NotImplementedError

    def _build_options(self, options):
        options = options if options else {}
        if options.get("reconnect_delay") == None:
            options["reconnect_delay"] = 1
        if options.get("ping_interval") == None:
            options["ping_interval"] = None
        return options


class Connection(AbstractConnection):
    # ===========================================
    # apis
    # ===========================================
    def __init__(self, endpoint: str, options={}, event_handler=None, loop=None):
        super().__init__()

        self.__endpoint = endpoint
        self.__options = self._build_options(options)
        self.__event_handler = event_handler if event_handler else EventHandler()
        self.__loop = loop if loop else asyncio.get_event_loop()

        self.__should_run = True
        self.__repeat_reconnect_task = None
        self.__repeat_receive_task = None

        self.__websocket = None
        self.__open_event = asyncio.Event()
        self.__closed_event = asyncio.Event()
        self.__last_ref = 0
        self.__request_futures = {}

        self.__toggle_to_close()
        self.__add_repeat_reconnect_task()
        self.__add_repeat_receive_task()

    async def close(self):
        self.__should_run = False
        self.__delete_repeat_receive_task()
        self.__delete_repeat_reconnect_task()
        await self.__disconnect()

    def endpoint(self):
        return self.__endpoint

    def is_open(self):
        return self.__websocket != None and self.__websocket.open == True

    async def wait_open(self):
        return await self.__open_event.wait()

    async def request(self, msg):
        ref = self.__next_ref()
        msg.ref = ref

        request_future = self.__loop.create_future()
        self.__request_futures[ref] = request_future
        try:
            await self.__send(msg)
            return await request_future
        except Exception as e:
            raise e
        finally:
            self.__request_futures.pop(ref, None)

    # ===========================================
    # tasks
    # ===========================================
    def __add_repeat_reconnect_task(self):
        if self.__repeat_reconnect_task is None:
            self.__repeat_reconnect_task = self.__loop.create_task(
                self.__repeat_reconnect()
            )

    def __add_repeat_receive_task(self):
        if self.__repeat_receive_task is None:
            self.__repeat_receive_task = self.__loop.create_task(
                self.__repeat_receive()
            )

    def __delete_repeat_reconnect_task(self):
        if self.__repeat_reconnect_task is not None:
            self.__repeat_reconnect_task.cancel()
            self.__repeat_reconnect_task = None

    def __delete_repeat_receive_task(self):
        if self.__repeat_receive_task is not None:
            self.__repeat_receive_task.cancel()
            self.__repeat_receive_task = None

    # ===========================================
    # connection management
    # ===========================================
    async def __repeat_reconnect(self):
        while self.__should_run:
            await self.__closed_event.wait()
            await self.__disconnect()
            await self.__connect()

    async def __connect(self):
        try:
            self.__on_connecting()
            self.__websocket = await websockets.connect(
                uri=self.__build_url(),
                ping_interval=self.__options.get("ping_interval"),
                max_size=None,
            )
            self.__toggle_to_open()
            self.__on_connected()
        except Exception as e:
            logger.error("Failed to connect: %s", e)
            self.__on_error(ErrorCode.FAILED_TO_CONNECT)
            if self.__should_run:
                await asyncio.sleep(self.__options.get("reconnect_delay"))

    async def __disconnect(self):
        try:
            if self.__websocket is not None:
                self.__on_disconnecting()
                await self.__websocket.close()
                self.__websocket = None
                self.__on_disconnected()
        except Exception as e:
            logger.error("Failed to disconnect: %s", e)
            self.__on_error(ErrorCode.FAILED_TO_DISCONNECT)
        finally:
            self.__toggle_to_close()

    def __toggle_to_open(self):
        self.__open_event.set()
        self.__closed_event.clear()

    def __toggle_to_close(self):
        self.__open_event.clear()
        self.__closed_event.set()

    # ===========================================
    # msg communication
    # ===========================================
    async def __send(self, msg):
        encoded_msg = None
        try:
            encoded_msg = protocol.encode_msg(msg)
        except Exception as e:
            logger.error("Failed to encode: %s", e)
            self.__on_error(ErrorCode.FAILED_TO_ENCODE)
            raise Error(ErrorCode.FAILED_TO_ENCODE, f"""Failed to encode: {msg}""")

        try:
            await self.__websocket.send(encoded_msg)
        except websockets.ConnectionClosed:
            logger.warning("Connection closed: endpoint: %s", self.__endpoint)
            self.__toggle_to_close()
            self.__on_error(ErrorCode.FAILED_TO_SEND)
            raise Error(
                ErrorCode.FAILED_TO_SEND,
                f"""Connection closed: endpoint: {self.__endpoint}""",
            )
        except Exception as e:
            logger.error(
                "Failed to send: length: %s, error: %s",
                len(encoded_msg),
                e,
            )
            self.__on_error(ErrorCode.FAILED_TO_SEND)
            raise Error(ErrorCode.FAILED_TO_SEND, f"""Failed to send: {msg}""")

    async def __repeat_receive(self):
        while self.__should_run:
            await self.__open_event.wait()
            await self.__receive()

    async def __receive(self):
        try:
            encoded_msg = await self.__websocket.recv()
        except websockets.ConnectionClosed:
            logger.warning("Connection closed: endpoint: %s", self.__endpoint)
            self.__toggle_to_close()
            self.__on_error(ErrorCode.FAILED_TO_RECEIVE)
            return
        except Exception as e:
            logger.error("Failed to receive: %s", e)
            self.__toggle_to_close()
            self.__on_error(ErrorCode.FAILED_TO_RECEIVE)
            return

        try:
            msg = protocol.decode_msg(encoded_msg)
            request_future = self.__request_futures.get(msg.ref)
            if request_future is None:
                logger.error("Failed to find future: msg: %s", msg)
                return
            if (
                msg.__class__ == protocol_types.error_rep_t
                or msg.__class__ == protocol_types.error2_rep_t
            ):
                request_future.set_exception(Error(msg.code, msg.desc))
            else:
                request_future.set_result(msg)
        except Exception as e:
            logger.error("Failed to decode: %s", e)
            self.__on_error(ErrorCode.FAILED_TO_DECODE)

    # ===========================================
    # listeners
    # ===========================================
    def __on_connecting(self):
        logger.info("Connecting to endpoint: %s", self.__endpoint)
        self.__event_handler.on_connecting(self)
        self.notify(Event.ON_CONNECTING, self)

    def __on_connected(self):
        logger.info("Connected to endpoint: %s", self.__endpoint)
        self.__event_handler.on_connected(self)
        self.notify(Event.ON_CONNECTED, self)

    def __on_disconnecting(self):
        logger.info("Disconnecting from endpoint: %s", self.__endpoint)
        self.__event_handler.on_disconnecting(self)
        self.notify(Event.ON_DISCONNECTING, self)

    def __on_disconnected(self):
        logger.info("Disconnected from endpoint: %s", self.__endpoint)
        self.__event_handler.on_disconnected(self)
        self.notify(Event.ON_DISCONNECTED, self)

    def __on_error(self, code):
        logger.error("Error occured: %s", code)
        self.__event_handler.on_error(code, self)
        self.notify(Event.ON_ERROR, code, self)

    # ===========================================
    # utils
    # ===========================================

    def __next_ref(self):
        new_ref = self.__last_ref + 1
        if new_ref > 2100000000:
            new_ref = 1
        self.__last_ref = new_ref
        return new_ref

    def __build_url(self):
        return "ws://" + self.__endpoint + "/$ws"


class MultiAltEndpointsConnection(AbstractConnection):
    # ===========================================
    # apis
    # ===========================================
    def __init__(
        self,
        pick_endpoint: Callable[[], str],
        options={},
        event_handler=None,
        loop=None,
    ):
        super().__init__()

        self.__pick_endpoint = pick_endpoint
        self.__options = self._build_options(options)
        self.__event_handler = event_handler if event_handler else EventHandler()
        self.__loop = loop if loop else asyncio.get_event_loop()

        self.__should_run = True
        self.__connection: Connection = None
        self.__open_event = asyncio.Event()
        self.__connect_task = None

        self.__toggle_to_close()
        self.__add_connect_task()

    async def close(self):
        self.__should_run = False
        self.__delete_connect_task()
        if self.__connection is not None:
            await self.__connection.close()

    def endpoint(self):
        if self.__connection is None:
            return None
        return self.__connection.endpoint()

    def is_open(self):
        return self.__connection != None and self.__connection.is_open()

    async def wait_open(self):
        return await self.__open_event.wait()

    async def request(self, msg):
        return await self.__connection.request(msg)

    # ===========================================
    # EventHandler implementation
    # ===========================================
    def on_connecting(self, connection: Connection):
        self.__event_handler.on_connecting(self, connection)
        self.notify(Event.ON_CONNECTING, self, connection)

    def on_connected(self, connection: Connection):
        self.__toggle_to_open()
        self.__event_handler.on_connected(self, connection)
        self.notify(Event.ON_CONNECTED, self, connection)

    def on_disconnecting(self, connection: Connection):
        self.__event_handler.on_disconnecting(self, connection)
        self.notify(Event.ON_DISCONNECTING, self, connection)

    def on_disconnected(self, connection: Connection):
        self.__toggle_to_close()
        self.__event_handler.on_disconnected(self, connection)
        self.notify(Event.ON_DISCONNECTED, self, connection)

    def on_error(self, error_code, connection: Connection):
        if error_code == ErrorCode.FAILED_TO_CONNECT:
            self.__reconnect()
        self.__event_handler.on_error(error_code, self, connection)
        self.notify(Event.ON_ERROR, error_code, self, connection)

    # ===========================================
    # internal functions
    # ===========================================
    def __toggle_to_open(self):
        self.__open_event.set()

    def __toggle_to_close(self):
        self.__open_event.clear()

    def __add_connect_task(self, delay=None):
        if self.__connect_task is None:
            self.__connect_task = self.__loop.create_task(self.__connect(delay))

    def __delete_connect_task(self):
        if self.__connect_task is not None:
            self.__connect_task.cancel()
            self.__connect_task = None

    async def __connect(self, delay):
        try:
            old_connection = self.__connection
            if old_connection is not None:
                await old_connection.close()

            if delay is not None:
                await asyncio.sleep(delay)

            endpoint = await self.__pick_endpoint()
            self.__connection = Connection(
                endpoint=endpoint,
                options=self.__options,
                event_handler=self,
                loop=self.__loop,
            )
        except Exception as e:
            logger.error("Failed to connect: %s", e)
            self.__reconnect()

    def __reconnect(self):
        if self.__should_run:
            self.__delete_connect_task()
            self.__add_connect_task(self.__options.get("reconnect_delay"))
