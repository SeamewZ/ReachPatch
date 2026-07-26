REGISTRY = {}


def register(name, callback):
    REGISTRY[name] = callback


class Box:
    def __init__(self, values):
        self.values = list(values)

    def __bool__(self):
        return bool(self.values)

    def __len__(self):
        return len(self.values)

    def __add__(self, other):
        if isinstance(other, Box):
            return Box(self.values + other.values)
        return NotImplemented

    def __radd__(self, other):
        if other == 0:
            return self
        return NotImplemented


def normalize(value):
    if not value:
        return []
    if isinstance(value, Box):
        return list(value.values)
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError("unsupported value")


def public(value):
    return normalize(value)


def safe_public(value):
    try:
        return public(value)
    except ValueError:
        return []


def dispatch(name, value):
    callback = REGISTRY[name]
    return callback(value)


register("normalize", normalize)
