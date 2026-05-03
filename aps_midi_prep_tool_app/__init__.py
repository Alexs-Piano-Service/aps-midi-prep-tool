from .app_info import APP_VERSION

__version__ = APP_VERSION


def main(*args, **kwargs):
    from .app import main as app_main

    return app_main(*args, **kwargs)


__all__ = ["main", "__version__"]
