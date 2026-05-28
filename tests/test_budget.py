from agent.budget import BudgetConfig, BudgetExceeded, BudgetSentinel


def test_budget_halts_on_tool_calls():
    sentinel = BudgetSentinel(BudgetConfig(max_tool_calls=1))
    try:
        sentinel.record_tool_call(success=True)
    except BudgetExceeded as exc:
        assert exc.reason == "MAX_TOOL_CALLS"
    else:
        raise AssertionError("Expected BudgetExceeded")
