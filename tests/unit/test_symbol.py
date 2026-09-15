from vo.market import Symbol


def test_symbol_creation():
    symbol = Symbol(
        broker_symbol="US100.n",
        description="Nasdaq 100",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.10,
        contract_size=1.0,
        source="test",
    )

    assert symbol.broker_symbol == "US100.n"
    assert symbol.description == "Nasdaq 100"
    assert symbol.digits == 2
    assert symbol.point == 0.01
    assert symbol.tick_size == 0.01
    assert symbol.tick_value == 0.10
    assert symbol.contract_size == 1.0

import pytest

from vo.market import Symbol


def test_symbol_rejects_negative_digits():
    with pytest.raises(ValueError):
        Symbol(
            broker_symbol="US100.n",
            description="Nasdaq 100",
            digits=-1,
            point=0.01,
            tick_size=0.01,
            tick_value=0.10,
            contract_size=1.0,
            source="test",
        )
def test_symbol_rejects_non_positive_point():
    with pytest.raises(ValueError):
        Symbol(
            broker_symbol="US100.n",
            description="Nasdaq 100",
            digits=2,
            point=0.0,
            tick_size=0.01,
            tick_value=0.10,
            contract_size=1.0,
            source="test",
        )
def test_symbol_rejects_non_positive_tick_size():
    with pytest.raises(ValueError):
        Symbol(
            broker_symbol="US100.n",
            description="Nasdaq 100",
            digits=2,
            point=0.01,
            tick_size=0.0,
            tick_value=0.10,
            contract_size=1.0,
            source="test",
        )
def test_symbol_rejects_empty_broker_symbol():
    with pytest.raises(ValueError):
        Symbol(
            broker_symbol="",
            description="Nasdaq 100",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=0.10,
            contract_size=1.0,
            source="test",
        )
def test_symbol_rejects_empty_source():
    with pytest.raises(ValueError):
        Symbol(
            broker_symbol="US100.n",
            description="Nasdaq 100",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=0.10,
            contract_size=1.0,
            source="",
        )