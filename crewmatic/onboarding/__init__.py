def __getattr__(name):
    if name == "SetupWizard":
        from .wizard import SetupWizard
        return SetupWizard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["SetupWizard"]
