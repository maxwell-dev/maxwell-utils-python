import pytest
import maxwell.protocol.maxwell_protocol_pb2 as protocol_types
from maxwell.utils.connection import Connection, MultiAltEndpointsConnection
from maxwell.utils.logger import get_logger

logger = get_logger(__name__)


class TestConnection:
    @pytest.mark.asyncio
    async def test_all(self):
        conn = Connection(
            endpoint="localhost:8081",
        )
        await conn.wait_open()
        msg = protocol_types.ping_req_t()
        reply = await conn.request(msg)
        expected = protocol_types.ping_rep_t()
        expected.ref = 1
        assert reply == expected
        await conn.close()


class TestMultiAltEndpointsConnection:
    async def __pick_endpoint(self):
        return "localhost:8081"

    @pytest.mark.asyncio
    async def test_all(self, event_loop):
        conn = MultiAltEndpointsConnection(
            pick_endpoint=self.__pick_endpoint,
            loop=event_loop,
        )
        await conn.wait_open()
        msg = protocol_types.ping_req_t()
        reply = await conn.request(msg)
        expected = protocol_types.ping_rep_t()
        expected.ref = 1
        assert reply == expected
        await conn.close()
