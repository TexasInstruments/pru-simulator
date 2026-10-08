"""The stdio server must advertise JSON Schema types that match the annotations."""

import inspect
import typing

import pytest

from mcp_server.server import PRUSimulatorMCP, _annotation_to_json_schema, _tool_input_schema


def tool_schemas() -> dict:
    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    return {
        name: _tool_input_schema(method)
        for name, method in inspect.getmembers(mcp, predicate=inspect.ismethod)
        if not name.startswith("_")
    }


def prop(tool: str, param: str) -> dict:
    return tool_schemas()[tool]["properties"][param]


def test_scalar_parameters_keep_their_types():
    assert prop("pru_step", "count") == {"type": "integer", "default": 1}
    assert prop("pru_set_input", "value") == {"type": "boolean", "default": False}
    assert prop("pru_step", "core") == {"type": "string", "default": "pru0"}
    assert prop("pru_memory", "addr") == {"type": "integer"}
    assert "addr" in tool_schemas()["pru_memory"]["required"]


def test_optional_list_of_strings_is_an_array_of_strings():
    schema = tool_schemas()["pru_load"]
    assert schema["properties"]["include_paths"] == {
        "type": "array",
        "items": {"type": "string"},
        "default": None,
    }
    assert "include_paths" not in schema["required"]
    assert schema["required"] == ["source"]


def test_list_of_ints_is_an_array_of_integers():
    schema = tool_schemas()["pru_uart_inject"]
    assert schema["properties"]["payload"] == {"type": "array", "items": {"type": "integer"}}
    assert "payload" in schema["required"]


@pytest.mark.parametrize(
    "annotation, expected",
    [
        (int, {"type": "integer"}),
        (float, {"type": "number"}),
        (bool, {"type": "boolean"}),
        (str, {"type": "string"}),
        (int | None, {"type": "integer"}),
        (typing.Optional[int], {"type": "integer"}),
        (int | float, {"type": "number"}),
        (float | int | None, {"type": "number"}),
        (list, {"type": "array"}),
        (list[int], {"type": "array", "items": {"type": "integer"}}),
        (list[str] | None, {"type": "array", "items": {"type": "string"}}),
        (dict, {"type": "object"}),
        (dict[str, int], {"type": "object"}),
        (dict | None, {"type": "object"}),
        (inspect.Parameter.empty, {"type": "string"}),
    ],
)
def test_annotation_mapping(annotation, expected):
    assert _annotation_to_json_schema(annotation) == expected


def test_no_property_is_advertised_as_string_unless_annotated_str():
    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    checked = 0
    for name, method in inspect.getmembers(mcp, predicate=inspect.ismethod):
        if name.startswith("_"):
            continue
        schema = _tool_input_schema(method)
        for param_name, param in inspect.signature(method).parameters.items():
            if param_name == "self":
                continue
            checked += 1
            advertised = schema["properties"][param_name].get("type")
            if param.annotation is str:
                assert advertised == "string", (name, param_name)
            else:
                assert advertised != "string", (name, param_name, param.annotation)
    assert checked > 10


def test_optional_int_mask_is_an_integer():
    schema = tool_schemas()["pru_gpio_drive_mask"]
    assert schema["properties"]["mask"] == {"type": "integer", "default": None}
    assert "mask" not in schema["required"]


def test_optional_dict_config_is_an_object():
    schema = tool_schemas()["pru_device_attach"]
    assert schema["properties"]["config"] == {"type": "object", "default": None}
    assert "config" not in schema["required"]
