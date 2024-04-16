import json
import logging
import logging.config
import os
import sysconfig

inited = False


def __get_root_dir():
    """Get the root directory of the project."""
    root_dir = os.environ.get("APP_ROOT_DIR")
    if root_dir:
        return root_dir
    return os.path.abspath(
        os.path.join(sysconfig.get_path("purelib"), "..", "..", "..", "..")
    )


def __init(
    default_path="logging.json", default_level=logging.INFO, env_var_key="LOG_CFG_FILE"
):
    root_dir = __get_root_dir()

    specified_config_path = os.getenv(env_var_key, None)
    if specified_config_path:
        config_path = specified_config_path
    else:
        config_path = os.path.join(root_dir, "config", default_path)

    if os.path.exists(config_path):
        with open(config_path, "rt") as config_file:
            config = json.load(config_file)
            log_dir = os.path.join(root_dir, "log")
            info_handler = config["handlers"]["info_file_handler"]
            warning_handler = config["handlers"]["warning_file_handler"]
            error_handler = config["handlers"]["error_file_handler"]
            info_handler["filename"] = os.path.join(log_dir, info_handler["filename"])
            warning_handler["filename"] = os.path.join(
                log_dir, warning_handler["filename"]
            )
            error_handler["filename"] = os.path.join(log_dir, error_handler["filename"])
        logging.config.dictConfig(config)
    else:
        logging.basicConfig(level=default_level)


def get_logger(name):
    global inited
    if not inited:
        __init()
        inited = True
    return logging.getLogger(name)


if __name__ == "__main__":
    logger = get_logger(__name__)
    logger.info("hello %s", "world")
