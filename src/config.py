import os
import os.path

import yaml


def load_config(config_file):
    with open(config_file) as file_stream:
        try:
            config = yaml.load(file_stream, Loader=yaml.FullLoader)
        except Exception as err:
            print("There appears to be a syntax problem with your config.yml")
            raise err

        # [section, type, error message]
        sections = [
            ["token", str, "Section `token` must be a string wrapped in quotes."],
            ["url", str, "Section `url` must be a string wrapped in quotes."],
            ["engine", dict, "Section `engine` must be a dictionary with indented keys followed by colons."],
            ["challenge", dict, "Section `challenge` must be a dictionary with indented keys followed by colons.."]]
        for section in sections:
            if section[0] not in config:
                raise Exception("Your config.yml does not have required section `{}`.".format(section[0]))
            elif not isinstance(config[section[0]], section[1]):
                raise Exception(section[2])

        # [section, type, error message]
        engine_sections = [["dir", str, "´dir´ must be a string wrapped in quotes."],
                           ["name", str, "´name´ must be a string wrapped in quotes."]]
        for subsection in engine_sections:
            if subsection[0] not in config["engine"]:
                raise Exception("Your config.yml does not have required `engine` subsection `{}`.".format(subsection))
            if not isinstance(config["engine"][subsection[0]], subsection[1]):
                raise Exception("´engine´ subsection {}".format(subsection[2]))

        if config["token"] == "xxxxxxxxxxxxxxxx":
            raise Exception("Your config.yml has the default Lichess API token. This is probably wrong.")

        if not os.path.isdir(config["engine"]["dir"]):
            raise Exception("Your engine directory `{}` is not a directory.")

        engine = os.path.join(config["engine"]["dir"], config["engine"]["name"])

        if not os.path.isfile(engine):
            raise Exception("The engine %s file does not exist." % engine)

        if not os.access(engine, os.X_OK):
            raise Exception("The engine %s doesn't have execute (x) permission. Try: chmod +x %s" % (engine, engine))

    return config
