#!/usr/bin/env python3
"""Minimal JSON Schema (Draft 2020-12) validator, standard library only.

Every JSON file the bot reads or writes has a schema in schemas/. Those schemas are the single
source of truth for the file format: editors that understand JSON Schema use them through the
file's "$schema" key, and the bot validates against them before using any data.

Supported keywords (the subset the bot's schemas use; anything else is rejected when the
schema is loaded, so an unsupported keyword can never be silently ignored):
  type, enum, const, properties, required, additionalProperties, patternProperties,
  propertyNames, items, minItems, maxItems, uniqueItems, minimum, maximum, minLength,
  maxLength, pattern, $ref (local "#/$defs/..."), $defs, oneOf, anyOf,
  and the annotations $schema, $id, title, description, default, examples, $comment,
  x-env (environment variable of a setting), x-file (pattern the file name must match, checked by
  read_document).

Standard library only, compatible with the macOS system Python (3.9).
"""

import json
import os
import re

SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "schemas")

ANNOTATIONS = {"$schema", "$id", "title", "description", "default", "examples", "$comment", "x-env", "x-file", "x-init"}
KEYWORDS = ANNOTATIONS | {
    "type", "enum", "const", "properties", "required", "additionalProperties",
    "patternProperties", "propertyNames", "items", "minItems", "maxItems", "uniqueItems",
    "minimum", "maximum", "minLength", "maxLength", "pattern", "$ref", "$defs", "oneOf", "anyOf",
}
TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    """The schema itself is malformed or uses an unsupported keyword."""


class ValidationError(Exception):
    """The document does not match its schema. `errors` lists every problem found."""

    def __init__(self, errors):
        super().__init__("\n".join(errors))
        self.errors = errors


_cache = {}


def load_schema(name):
    """schemas/<name>.schema.json, checked for unsupported keywords once."""
    if name not in _cache:
        path = os.path.join(SCHEMAS_DIR, f"{name}.schema.json")
        with open(path, encoding="utf-8") as handle:
            schema = json.load(handle)
        _check_keywords(schema, "#")
        _cache[name] = schema
    return _cache[name]


def _check_keywords(node, where):
    if not isinstance(node, dict):
        return
    unknown = set(node) - KEYWORDS
    if unknown:
        raise SchemaError(f"{where}: unsupported keyword(s): {', '.join(sorted(unknown))}")
    for key in ("properties", "patternProperties", "$defs"):
        for child, sub in node.get(key, {}).items():
            _check_keywords(sub, f"{where}/{key}/{child}")
    for key in ("items", "additionalProperties", "propertyNames"):
        if isinstance(node.get(key), dict):
            _check_keywords(node[key], f"{where}/{key}")
    for key in ("oneOf", "anyOf"):
        for index, sub in enumerate(node.get(key, [])):
            _check_keywords(sub, f"{where}/{key}/{index}")


def _resolve(root, ref):
    if not ref.startswith("#/"):
        raise SchemaError(f"only local $ref is supported: {ref}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _pointer(path):
    return "/" + "/".join(str(p) for p in path) if path else "(root)"


def _validate(value, schema, root, path, errors):
    if "$ref" in schema:
        _validate(value, _resolve(root, schema["$ref"]), root, path, errors)

    where = _pointer(path)

    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(TYPES[t](value) for t in allowed):
            errors.append(f"{where}: expected {' or '.join(allowed)}, got {json.dumps(value)[:60]}")
            return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must be {json.dumps(schema['const'])}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: must be one of {', '.join(json.dumps(v) for v in schema['enum'])}")

    for key, keyword in (("oneOf", 1), ("anyOf", None)):
        if key in schema:
            matches = 0
            for sub in schema[key]:
                sub_errors = []
                _validate(value, sub, root, path, sub_errors)
                matches += not sub_errors
            if (keyword == 1 and matches != 1) or (keyword is None and matches == 0):
                errors.append(f"{where}: does not match {'exactly one' if keyword else 'any'} of the allowed forms")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: shorter than {schema['minLength']} character(s)")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: longer than {schema['maxLength']} character(s)")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            hint = schema.get("description")
            errors.append(f"{where}: {hint.rstrip('.') if hint else 'does not match ' + schema['pattern']}"
                          f" (got {json.dumps(value)[:40]})")

    if TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: minimum is {schema['minimum']}, got {value}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: maximum is {schema['maximum']}, got {value}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{where}: at most {schema['maxItems']} item(s)")
        if schema.get("uniqueItems"):
            seen = []
            for item in value:
                if item in seen:
                    errors.append(f"{where}: duplicate item {json.dumps(item)[:60]}")
                    break
                seen.append(item)
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _validate(item, schema["items"], root, path + [index], errors)

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{where}: missing required key \"{name}\"")
        properties = schema.get("properties", {})
        patterns = [(re.compile(p), s) for p, s in schema.get("patternProperties", {}).items()]
        additional = schema.get("additionalProperties", True)
        names = schema.get("propertyNames")
        for key, item in value.items():
            if names is not None:
                _validate(key, names, root, path + [key], errors)
            matched = False
            if key in properties:
                _validate(item, properties[key], root, path + [key], errors)
                matched = True
            for regex, sub in patterns:
                if regex.search(key):
                    _validate(item, sub, root, path + [key], errors)
                    matched = True
            if not matched:
                if additional is False:
                    errors.append(f"{where}: unknown key \"{key}\"")
                elif isinstance(additional, dict):
                    _validate(item, additional, root, path + [key], errors)


def validate(value, name):
    """Raise ValidationError listing every mismatch of `value` against schemas/<name>."""
    schema = load_schema(name)
    errors = []
    _validate(value, schema, schema, [], errors)
    if errors:
        raise ValidationError(errors)


def schema_ref(name, from_dir):
    """Value for a file's "$schema": relative when the file lives inside the bot's directory
    (so the bot can be moved), absolute otherwise (a relative path climbing out of an unrelated
    directory would be long and break as soon as either side moves)."""
    target = os.path.join(SCHEMAS_DIR, f"{name}.schema.json")
    home = os.path.dirname(SCHEMAS_DIR)
    from_dir = os.path.realpath(from_dir)
    if from_dir == os.path.realpath(home) or from_dir.startswith(os.path.realpath(home) + os.sep):
        return os.path.relpath(target, from_dir)
    return target


def no_duplicates(label):
    """object_pairs_hook rejecting duplicate keys, which json.load would silently collapse."""
    def hook(pairs):
        seen = {}
        for key, value in pairs:
            if key in seen:
                raise ValidationError([f"{label}: duplicate key \"{key}\""])
            seen[key] = value
        return seen
    return hook


def read_document(path, name, required=True):
    """Parse and validate a JSON file. Returns None when it does not exist and is optional.
    Raises ValidationError with file-prefixed messages for parse and schema errors."""
    label = os.path.basename(path)
    if not os.path.exists(path):
        if required:
            raise ValidationError([f"{label}: not found in {os.path.dirname(path)}"])
        return None
    pattern = load_schema(name).get("x-file")
    if pattern and not re.match(pattern, label):
        raise ValidationError([f"{label}: the file name must match {pattern}"])
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=no_duplicates(label))
    except OSError as error:
        raise ValidationError([f"{label}: cannot read ({error.strerror})"])
    except ValueError as error:
        where = f":{error.lineno}:{error.colno}" if hasattr(error, "lineno") else ""
        raise ValidationError([f"{label}{where}: invalid JSON: {getattr(error, 'msg', error)}"])
    data = migrate_legacy(data, name)
    try:
        validate(data, name)
    except ValidationError as error:
        raise ValidationError([f"{label}: {message}" for message in error.errors])
    return data


def migrate_legacy(data, name):
    """Upgrade cached v1 Important/Nit review data in memory to the current severity model."""
    if not isinstance(data, dict):
        return data
    old_severity = {"Important": "High", "Nit": "Low"}
    if name == "settings":
        review = data.get("review")
        if isinstance(review, dict) and "max_nits" in review and "max_lows" not in review:
            data = json.loads(json.dumps(data))
            data["review"]["max_lows"] = data["review"].pop("max_nits")
        return data
    if data.get("version") != 1 or name not in ("candidates", "verdicts", "findings"):
        return data
    data = json.loads(json.dumps(data))
    data["version"] = 2
    if name == "candidates":
        for finding in data.get("candidates", []):
            finding["severity"] = old_severity.get(finding.get("severity"), finding.get("severity"))
    elif name == "verdicts":
        if "severity" in data:
            data["severity"] = old_severity.get(data["severity"], data["severity"])
    else:
        for finding in data.get("findings", []):
            finding["severity"] = old_severity.get(finding.get("severity"), finding.get("severity"))
        kept = [f for f in data.get("findings", [])
                if f.get("verification", {}).get("outcome") == "keep"]
        blocking = any(f["severity"] in ("Critical", "High") and not f.get("pre_existing") for f in kept)
        review_needed = any(f["severity"] in ("Critical", "High", "Medium") for f in kept)
        data["verdict"] = "REQUEST_CHANGES" if blocking else ("COMMENT" if review_needed else "PASS")
    return data
