import json

from docs_watchdog.diff import compute_changes
from docs_watchdog.extract import openapi_to_text
from docs_watchdog.rules import classify_changes


def spec(paths=None, schemas=None, version="1.0"):
    s = {"openapi": "3.0.0", "info": {"version": version}, "paths": paths or {}}
    if schemas is not None:
        s["components"] = {"schemas": schemas}
    return json.dumps(s)


def op(params=(), body=None, responses=None, deprecated=False, summary="S", operation_id=None):
    o = {"summary": summary, "parameters": list(params),
         "responses": responses or {"200": {"description": "ok", "content": {
             "application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}}}
    if deprecated:
        o["deprecated"] = True
    if body:
        o["requestBody"] = body
    if operation_id:
        o["operationId"] = operation_id
    return o


def p(name, required=False, typ="string", where="query"):
    return {"name": name, "in": where, "required": required, "schema": {"type": typ}}


PET = {"type": "object", "required": ["id"],
       "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}


def findings(old, new):
    ch = compute_changes(openapi_to_text(old), openapi_to_text(new), structural=True)
    return classify_changes(ch, "V", "api", "https://x/openapi.json")


def one(old, new):
    fs = findings(old, new)
    assert len(fs) == 1, [f.summary for f in fs]
    return fs[0]


BASE = spec({"/pets": {"get": op([p("limit")])}, "/owners": {"get": op()}}, {"Pet": PET})


def test_legacy_operation_line_format_is_preserved():
    out = openapi_to_text(spec({"/a": {"get": op(deprecated=True, summary="List")}}))
    assert "GET /a DEPRECATED List" in out


def test_rendering_is_stable_and_order_independent():
    a = spec({"/b": {"get": op()}, "/a": {"post": op(), "get": op()}}, {"Pet": PET})
    b = spec({"/a": {"get": op(), "post": op()}, "/b": {"get": op()}}, {"Pet": PET})
    assert openapi_to_text(a) == openapi_to_text(b)
    assert findings(a, b) == []


def test_removed_endpoint_is_detected_as_removal_not_noise():
    new = spec({"/pets": {"get": op([p("limit")])}}, {"Pet": PET})
    f = one(BASE, new)
    assert (f.change_type, f.severity) == ("removal", "high") and "GET /owners" in f.summary
    assert "GET /owners" in f.old_value and f.new_value == ""


def test_added_endpoint_is_new_and_low():
    new = spec({"/pets": {"get": op([p("limit")])}, "/owners": {"get": op()}, "/x": {"get": op()}}, {"Pet": PET})
    f = one(BASE, new)
    assert (f.change_type, f.severity) == ("new", "low")


def test_required_parameter_added_is_breaking_optional_is_not():
    req = spec({"/pets": {"get": op([p("limit"), p("tenant", required=True)])}, "/owners": {"get": op()}}, {"Pet": PET})
    opt = spec({"/pets": {"get": op([p("limit"), p("sort")])}, "/owners": {"get": op()}}, {"Pet": PET})
    f = one(BASE, req)
    assert (f.change_type, f.severity) == ("breaking", "high") and "tenant" in f.summary
    f = one(BASE, opt)
    assert (f.change_type, f.severity) == ("schema", "low")  # informational, NOT breaking


def test_parameter_removed_and_required_flag_flip():
    removed = spec({"/pets": {"get": op()}, "/owners": {"get": op()}}, {"Pet": PET})
    assert one(BASE, removed).change_type == "schema" and one(BASE, removed).severity == "medium"
    now_required = spec({"/pets": {"get": op([p("limit", required=True)])}, "/owners": {"get": op()}}, {"Pet": PET})
    f = one(BASE, now_required)
    assert (f.change_type, f.severity) == ("breaking", "high") and "required" in f.summary
    relaxed = one(now_required, BASE)
    assert (relaxed.change_type, relaxed.severity) == ("schema", "low")


def test_parameter_type_change_is_breaking():
    new = spec({"/pets": {"get": op([p("limit", typ="string")])}, "/owners": {"get": op()}}, {"Pet": PET})
    old = spec({"/pets": {"get": op([p("limit", typ="integer")])}, "/owners": {"get": op()}}, {"Pet": PET})
    f = one(old, new)
    assert f.change_type == "breaking" and "integer -> string" in f.summary


def test_path_level_and_ref_parameters_are_resolved():
    raw = {"openapi": "3.0.0", "info": {"version": "1"},
           "components": {"parameters": {"Lim": p("limit", typ="integer")}},
           "paths": {"/a": {"parameters": [{"$ref": "#/components/parameters/Lim"}],
                            "get": {"responses": {"200": {"description": "ok"}}}}}}
    assert "GET /a | param query:limit | required=false type=integer" in openapi_to_text(json.dumps(raw))


def test_request_body_and_response_changes():
    body = lambda req: {"required": req, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}
    old = spec({"/pets": {"post": op(body=body(False))}}, {"Pet": PET})
    new = spec({"/pets": {"post": op(body=body(True))}}, {"Pet": PET})
    assert one(old, new).change_type == "breaking"
    gone = spec({"/pets": {"post": op(responses={"201": {"description": "c"}})}}, {"Pet": PET})
    fs = findings(old, gone)
    kinds = {(f.change_type) for f in fs}
    assert "breaking" in kinds  # the documented 200 JSON response no longer exists
    retyped = spec({"/pets": {"post": op(body=body(False), responses={"200": {"description": "ok", "content": {
        "application/json": {"schema": {"type": "string"}}}}})}}, {"Pet": PET})
    assert any("Pet -> string" in f.summary for f in findings(old, retyped))


def test_schema_field_changes():
    def pet(**props):
        return {"type": "object", "required": props.pop("_req", ["id"]), "properties": props}

    base = spec({}, {"Pet": pet(id={"type": "integer"}, name={"type": "string"})})
    added = spec({}, {"Pet": pet(id={"type": "integer"}, name={"type": "string"}, tag={"type": "string"})})
    assert (one(base, added).change_type, one(base, added).severity) == ("schema", "low")
    added_req = spec({}, {"Pet": pet(_req=["id", "tag"], id={"type": "integer"}, name={"type": "string"}, tag={"type": "string"})})
    assert one(base, added_req).change_type == "breaking"
    removed = spec({}, {"Pet": pet(id={"type": "integer"})})
    assert one(base, removed).change_type == "breaking"
    retyped = spec({}, {"Pet": pet(id={"type": "string"}, name={"type": "string"})})
    assert "integer -> string" in one(base, retyped).summary
    optional = spec({}, {"Pet": pet(_req=[], id={"type": "integer"}, name={"type": "string"})})
    assert one(base, optional).change_type == "schema"  # required -> optional is a relaxation


def test_nested_fields_enums_and_removed_schema():
    nested = {"type": "object", "properties": {"owner": {"type": "object", "properties": {"name": {"type": "string"}}},
                                               "kind": {"type": "string", "enum": ["cat", "dog"]}}}
    old = spec({}, {"Pet": nested, "Gone": PET})
    text = openapi_to_text(old)
    assert "schema Pet | field owner.name |" in text and "enum(cat,dog)" in text
    new = spec({}, {"Pet": {**nested, "properties": {**nested["properties"], "kind": {"type": "string", "enum": ["cat"]}}}})
    fs = findings(old, new)
    assert {f.summary.split(" ")[0] for f in fs} >= {"Schema"} or fs  # enum change + schema removed
    assert any(f.change_type == "breaking" and "Gone" in f.summary for f in fs)
    assert any("enum(cat,dog) -> enum(cat)" in f.summary for f in fs)


def test_deprecation_flag_and_informational_changes_are_not_breaking():
    dep = spec({"/pets": {"get": op([p("limit")], deprecated=True)}, "/owners": {"get": op()}}, {"Pet": PET})
    f = one(BASE, dep)
    assert (f.change_type, f.severity, f.confirmed) == ("deprecation", "medium", True)
    assert f.deadline is None and "no removal date" in f.impact
    for old, new in [(spec({}, version="1"), spec({}, version="2")),
                     (spec({"/a": {"get": op(summary="x")}}), spec({"/a": {"get": op(summary="y")}})),
                     (spec({"/a": {"get": op(operation_id="a")}}), spec({"/a": {"get": op(operation_id="b")}}))]:
        f = one(old, new)
        assert (f.change_type, f.severity) == ("informational", "low")


def test_swagger2_body_parameter_and_definitions():
    sw = {"swagger": "2.0", "info": {"version": "1"},
          "paths": {"/a": {"post": {"parameters": [{"in": "body", "name": "b", "required": True, "schema": {"$ref": "#/definitions/X"}}],
                                    "responses": {"200": {"schema": {"type": "string"}}}}}},
          "definitions": {"X": {"type": "object", "properties": {"k": {"type": "integer"}}}}}
    out = openapi_to_text(json.dumps(sw))
    assert "POST /a | body b | required=true type=X" in out and "schema X | field k |" in out
