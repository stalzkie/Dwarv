from dwarv.repo.graph import build_graph, query_relevant_context


def _write_sample_repo(root):
    (root / "app.py").write_text(
        "def helper(x):\n    return x * 2\n\n\ndef add(a, b):\n    return helper(a) + helper(b)\n",
        encoding="utf-8",
    )
    (root / "models.py").write_text(
        "class Cache:\n"
        "    def __init__(self):\n"
        "        self._store = {}\n"
        "\n"
        "    def get(self, key):\n"
        "        return self._store.get(key)\n"
        "\n"
        "    def put(self, key, value):\n"
        "        self._store[key] = value\n",
        encoding="utf-8",
    )


def test_build_graph_finds_functions_and_classes(tmp_path):
    _write_sample_repo(tmp_path)

    graph = build_graph(tmp_path)

    assert "app.py::helper" in graph.symbols
    assert "app.py::add" in graph.symbols
    assert "models.py::Cache" in graph.symbols
    assert "models.py::Cache.get" in graph.symbols
    assert "models.py::Cache.put" in graph.symbols
    assert graph.symbols["app.py::helper"].kind == "function"
    assert graph.symbols["models.py::Cache"].kind == "class"


def test_build_graph_captures_real_source_text(tmp_path):
    _write_sample_repo(tmp_path)

    graph = build_graph(tmp_path)

    assert graph.symbols["app.py::helper"].source == "def helper(x):\n    return x * 2"


def test_build_graph_resolves_calls_both_directions(tmp_path):
    _write_sample_repo(tmp_path)

    graph = build_graph(tmp_path)

    assert "app.py::helper" in graph.calls["app.py::add"]
    assert "app.py::add" in graph.called_by["app.py::helper"]


def test_build_graph_skips_unparseable_files_without_crashing(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n    pass", encoding="utf-8")
    (tmp_path / "app.py").write_text("def ok():\n    pass\n", encoding="utf-8")

    graph = build_graph(tmp_path)

    assert "app.py::ok" in graph.symbols
    assert "broken.py" not in graph.file_symbols


def test_query_relevant_context_matches_mentioned_symbol(tmp_path):
    _write_sample_repo(tmp_path)
    graph = build_graph(tmp_path)

    context = query_relevant_context(graph, "fix the bug in add")

    assert "def add(a, b):" in context
    # one-hop neighbor (add calls helper) is pulled in too
    assert "def helper(x):" in context
    # unrelated symbol is not pulled in
    assert "class Cache" not in context


def test_query_relevant_context_is_smaller_than_whole_repo_dump(tmp_path):
    _write_sample_repo(tmp_path)
    graph = build_graph(tmp_path)

    whole_repo_chars = sum(len(graph.symbols[sid].source) for sid in graph.symbols)
    context = query_relevant_context(graph, "fix the bug in add")

    assert len(context) < whole_repo_chars


def test_query_relevant_context_falls_back_to_table_of_contents(tmp_path):
    _write_sample_repo(tmp_path)
    graph = build_graph(tmp_path)

    context = query_relevant_context(graph, "what does this repo do in general")

    assert "app.py" in context
    assert "models.py" in context
    assert "function helper" in context
    assert "class Cache" in context
    # no full source bodies in the fallback -- just a table of contents
    assert "return x * 2" not in context


def test_query_relevant_context_respects_char_budget(tmp_path):
    _write_sample_repo(tmp_path)
    graph = build_graph(tmp_path)

    generous = query_relevant_context(graph, "fix the bug in add", max_total_chars=4000)
    tiny = query_relevant_context(graph, "fix the bug in add", max_total_chars=10)

    assert len(tiny) < len(generous)
