"""
A thin seam, not a framework: order_manager.py and program_manager.py are
typed against this Protocol instead of TradejiniClient directly. Costs
almost nothing today -- TradejiniClient already satisfies it structurally,
no changes needed there -- but means a second broker later is "write a new
class that implements this," not "rewrite the order manager." Added
specifically because multi-broker execution is a stated future direction
for the Advanced OMS (spreading capital across brokers to avoid entry
limits/strike saturation on any single one), not because it's needed today.

Deliberately NOT an abc.ABC -- typing.Protocol gives structural typing
(duck typing checked by the type checker, no explicit inheritance
required), which is the right fit here since TradejiniClient was written
before this interface existed and shouldn't need to change to satisfy it.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class BrokerClient(Protocol):
    # NOT part of this protocol's required shape -- PaperBrokerClient deliberately has no
    # broker_id at all (paper isn't a real broker), so every call site reads this via
    # getattr(client, "broker_id", None) rather than assuming it's always present. Listed
    # here only as documentation of the attribute a REAL broker client is expected to carry.
    broker_id: str

    async def login(self) -> None: ...

    @property
    def auth_token(self) -> str: ...

    @property
    def is_logged_in(self) -> bool: ...

    async def place_order(self, **fields) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...
    async def get_orders(self) -> list[dict]: ...
    async def close(self) -> None: ...
