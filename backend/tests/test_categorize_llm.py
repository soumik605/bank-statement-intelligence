import json

import pytest

from app.categorize import categorize, categorize_with_llm, parse_llm_reply
from app.schema import Category, Transaction


def _txn(description, txn_type="debit", amount=100.0):
    return Transaction(date="2026-01-05", description=description, amount=amount, type=txn_type)


TXNS = [
    _txn("UPI-SWIGGY-1"),  # rules handle this one
    _txn("UPI/RAMESH K/OKAXIS"),
    _txn("POS 4521 LULU HYPERMARKET"),  # rules know DMART, not this
    _txn("NEFT CR-RAO FAMILY TRUST-GIFT", "credit", 400000),
]


class FakeLLM:
    def __init__(self, reply="[]", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return self.reply


def test_sends_only_uncategorized_in_one_batch():
    fake = FakeLLM('[{"id": 1, "category": "Transfer"}, {"id": 2, "category": "Food"}, {"id": 3, "category": "Transfer"}]')
    result = categorize(TXNS, complete=fake)

    assert len(fake.calls) == 1
    sent = json.loads(fake.calls[0][-1]["content"])
    assert sent == [
        {"id": 1, "description": "UPI/RAMESH K/OKAXIS", "amount": 100.0, "type": "debit"},
        {"id": 2, "description": "POS 4521 LULU HYPERMARKET", "amount": 100.0, "type": "debit"},
        {"id": 3, "description": "NEFT CR-RAO FAMILY TRUST-GIFT", "amount": 400000.0, "type": "credit"},
    ]
    assert [(t.category, t.category_source) for t in result] == [
        (Category.FOOD, "rule"), (Category.TRANSFER, "llm"), (Category.FOOD, "llm"), (Category.TRANSFER, "llm"),
    ]


def test_prompt_lists_every_category():
    fake = FakeLLM()
    categorize_with_llm(TXNS[1:], complete=fake)
    system = fake.calls[0][0]["content"]
    assert all(f"- {c.value}:" in system for c in Category)


def test_nothing_to_do_makes_no_call():
    fake = FakeLLM()
    done = [_txn("X").model_copy(update={"category": Category.FOOD, "category_source": "rule"})]
    assert categorize_with_llm(done, complete=fake) == done
    assert fake.calls == []


@pytest.mark.parametrize("reply", [
    '[{"id": 1, "category": "Transfer"}]',
    '```json\n[{"id": 1, "category": "Transfer"}]\n```',
    'Sure! Here are the categories:\n```\n[{"id": 1, "category": "Transfer"}]\n```\nLet me know if you need more.',
    '[{"id": "1", "category": " transfer "}]',  # string id, stray case and spaces
])
def test_parse_tolerates_wrapping(reply):
    assert parse_llm_reply(reply, {1}) == {1: Category.TRANSFER}


@pytest.mark.parametrize("reply", ["", "I can't help with that.", '[{"id": 1, "category": "Transfer"', "{}", "null"])
def test_parse_broken_reply_is_empty(reply):
    assert parse_llm_reply(reply, {1}) == {}


def test_parse_drops_bad_entries():
    reply = json.dumps([
        {"id": 1, "category": "Transfers"},  # not in the list
        {"id": 2, "category": "Food"},
        {"id": 2, "category": "Shopping"},  # repeat: first answer wins
        {"id": 99, "category": "Food"},  # not an id we sent
        {"id": True, "category": "Food"},
        "garbage",
    ])
    assert parse_llm_reply(reply, {1, 2, 3}) == {1: Category.OTHER, 2: Category.FOOD}


def test_broken_json_falls_back_to_other():
    result = categorize_with_llm(TXNS[1:], complete=FakeLLM("not json at all"))
    assert [(t.category, t.category_source) for t in result] == [(Category.OTHER, "llm")] * 3


def test_missing_id_and_wrong_direction_fall_back_to_other():
    # id 2 is missing; id 1 is a credit put in Food, which only takes debits.
    reply = '[{"id": 0, "category": "Transfer"}, {"id": 1, "category": "Food"}]'
    txns = [_txn("UPI/RAMESH K/OKAXIS"), _txn("UPI/ZOMATO/REFUND?", "credit"), _txn("POS 4521 DMART")]
    result = categorize_with_llm(txns, complete=FakeLLM(reply))
    assert [t.category for t in result] == [Category.TRANSFER, Category.OTHER, Category.OTHER]


def test_api_failure_falls_back_to_other():
    result = categorize_with_llm(TXNS[1:], complete=FakeLLM(error=RuntimeError("HTTP 503")))
    assert {t.category for t in result} == {Category.OTHER}
