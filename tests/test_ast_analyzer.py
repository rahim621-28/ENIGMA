from enigma.analysis.ast_analyzer import extract_symbols, find_symbol_at_line


def test_extract_symbols_finds_function(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    return 1\n")
    symbols = extract_symbols(str(f))
    assert len(symbols) == 1
    assert symbols[0].name == "foo"
    assert symbols[0].kind == "function"


def test_extract_symbols_finds_method_with_qualified_name(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("class Foo:\n    def bar(self):\n        return 1\n")
    symbols = extract_symbols(str(f))
    names = {s.name for s in symbols}
    assert "Foo" in names
    assert "Foo.bar" in names
    method = next(s for s in symbols if s.name == "Foo.bar")
    assert method.kind == "method"


def test_find_symbol_at_line_picks_innermost(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("class Foo:\n    def bar(self):\n        return 1\n")
    symbols = extract_symbols(str(f))
    hit = find_symbol_at_line(symbols, str(f), 3)
    assert hit is not None
    assert hit.name == "Foo.bar"


def test_find_symbol_at_line_returns_none_when_out_of_range(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    return 1\n")
    symbols = extract_symbols(str(f))
    assert find_symbol_at_line(symbols, str(f), 999) is None
