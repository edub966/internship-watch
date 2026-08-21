from src.sources.eightfold import EightfoldSource


def test_eightfold_source_is_configured_for_timestamp_ordering():
    # Guard the monitoring-specific choice against accidental regression back
    # to relevance ranking, which can reshuffle paginated results.
    import inspect
    source = inspect.getsource(EightfoldSource._request_page)
    assert '"sort_by": "timestamp"' in source
