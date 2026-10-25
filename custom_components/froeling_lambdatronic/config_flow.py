"""Config flow of the Fröling Lambdatronic integration (completed in the next step)."""

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Lambdatronic boiler."""

    VERSION = 1
