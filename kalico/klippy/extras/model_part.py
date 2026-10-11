# One part of the [model] tree
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import model


def load_config_prefix(config):
    return model.ModelPart(config)
