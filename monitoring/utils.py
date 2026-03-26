# device_id 정규화

def normalize_device_id(value: str) -> str:
    if value is None:
        return value

    value = value.strip().lower()

    if "_" in value:
        value = value.split("_")[-1]

    return value