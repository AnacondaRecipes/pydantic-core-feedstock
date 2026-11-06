"""Smoke check for the code path behind the deselected test.

It builds the schema from upstream test_default_validate_default_after_validator_field_name with
public API calls, then asserts that ValidationInfo.field_name reaches the validator.
If the shutdown crash (pydantic/pydantic#14242) were reachable from user code, this exits early.
"""

from pydantic_core import SchemaValidator, core_schema

seen = []


def record(value, info):
    seen.append(info.field_name)
    return value


schema = core_schema.model_schema(
    cls=type("M", (), {}),
    schema=core_schema.model_fields_schema(
        fields={
            "a": core_schema.model_field(
                schema=core_schema.with_default_schema(
                    schema=core_schema.with_info_after_validator_function(record, core_schema.str_schema()),
                    default="default",
                )
            )
        }
    ),
    config={"validate_default": True},
)
SchemaValidator(schema).validate_python({})
assert seen == ["a"], seen
