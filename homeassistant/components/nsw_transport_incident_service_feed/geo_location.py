"""Support for NSW Transport Feeds."""
from datetime import timedelta
import logging
import re
from typing import Optional

from aio_geojson_nsw_transport_incidents import NswTransportServiceIncidentsFeedManager
import voluptuous as vol

from homeassistant.components.geo_location import PLATFORM_SCHEMA, GeolocationEvent
from homeassistant.const import (  # ATTR_LOCATION,
    ATTR_ATTRIBUTION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import callback
from homeassistant.helpers import aiohttp_client, config_validation as cv
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

_LOGGER = logging.getLogger(__name__)

# ATTR_ATTRIBUTION = "attribution"
ATTR_TITLE = "title"
ATTR_CATEGORY = "category"
ATTR_EXTERNAL_ID = "external_id"
ATTR_PUBLICATION_DATE = "publication_date"
ATTR_DESCRIPTION = "description"
ATTR_PUBLICTRANSPORT = "publicTransport"
ATTR_ADVICEA = "adviceA"
ATTR_ADVICEB = "adviceB"
ATTR_ADVICEOTHER = "adviceOther"
ATTR_ISMAJOR = "isMajor"
ATTR_ISENDED = "isEnded"
ATTR_ISNEW = "isNew"
ATTR_ISIMPACTNETWORK = "isImpactNetwork"
ATTR_DIVERSIONS = "diversions"
ATTR_TYPE = "type"
ATTR_SUBCATEGORY = "subCategory"
ATTR_DURATION = "duration"
ATTR_FEATURE_TYPE = "featureType"
ATTR_ROAD = "road"
ATTR_COUNCIL = "council"

ATTR_PICTURE = "entity_picture"


CONF_CATEGORIES = "categories"
CONF_HAZARDS = "hazards"
CONF_HAZARDS_STATE = "hazardsState"


DEFAULT_RADIUS_IN_KM = 20.0
DEFAULT_UNIT_OF_MEASUREMENT = "km"

SCAN_INTERVAL = timedelta(minutes=5)

SIGNAL_DELETE_ENTITY = "nsw_transport_incident_service_feed_delete_{}"
SIGNAL_UPDATE_ENTITY = "nsw_transport_incident_service_feed_update_{}"

SOURCE = "nsw_transport_incident_service_feed"

VALID_HAZARDS = ["alpine", "fire", "flood", "incident", "majorevent", "roadwork"]
VALID_HAZARDS_STATE = ["open", "closed", "", None, "none", "None"]
DEFAULT_HAZARDS = "Incident"
DEFAULT_HAZARDS_STATE = "Open"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        # vol.Optional(CONF_HAZARDS, default=DEFAULT_HAZARDS): vol.All(
        #      vol.Coerce(str), vol.In(VALID_HAZARDS)
        # ),
        # vol.Optional(CONF_HAZARDS_STATE, default=DEFAULT_HAZARDS_STATE): vol.All(
        #     vol.Coerce(str), vol.In(VALID_HAZARDS_STATE)
        # ),
        vol.Optional(CONF_CATEGORIES, default=[]): cv.ensure_list,
        vol.Optional(CONF_HAZARDS, default=DEFAULT_HAZARDS): vol.All(
            vol.Coerce(str), vol.In(VALID_HAZARDS)
        ),
        vol.Optional(CONF_HAZARDS_STATE, default=DEFAULT_HAZARDS_STATE): vol.All(
            vol.Coerce(str), vol.In(VALID_HAZARDS_STATE)
        ),
        vol.Optional(CONF_LATITUDE): cv.latitude,
        vol.Optional(CONF_LONGITUDE): cv.longitude,
        vol.Optional(CONF_RADIUS, default=DEFAULT_RADIUS_IN_KM): vol.Coerce(float),
    }
)


async def async_setup_platform(
    hass: HomeAssistantType, config: ConfigType, async_add_entities, discovery_info=None
):
    """Set up the NSW Transport Feed platform."""
    scan_interval = config.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL)
    coordinates = (
        config.get(CONF_LATITUDE, hass.config.latitude),
        config.get(CONF_LONGITUDE, hass.config.longitude),
    )
    radius_in_km = config[CONF_RADIUS]
    categories = config.get(CONF_CATEGORIES)
    if config.get(CONF_HAZARDS_STATE).lower() == "none":
        hazard = f"{config.get(CONF_HAZARDS).lower()}"
    else:
        hazard = f"{config.get(CONF_HAZARDS).lower()}-{config.get(CONF_HAZARDS_STATE).lower()}"
    # Initialize the entity manager.
    manager = NswTransportServiceFeedEntityManager(
        hass,
        async_add_entities,
        scan_interval,
        coordinates,
        radius_in_km,
        categories,
        hazard,
    )

    async def start_feed_manager(event):
        """Start feed manager."""
        await manager.async_init()

    async def stop_feed_manager(event):
        """Stop feed manager."""
        await manager.async_stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, start_feed_manager)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_feed_manager)
    hass.async_create_task(manager.async_update())


class NswTransportServiceFeedEntityManager:
    """Feed Entity Manager for NSW Transport GeoJSON feed."""

    def __init__(
        self,
        hass,
        async_add_entities,
        scan_interval,
        coordinates,
        radius_in_km,
        categories,
        hazard,
    ):
        """Initialize the Feed Entity Manager."""
        self._hass = hass
        websession = aiohttp_client.async_get_clientsession(hass)
        self._feed_manager = NswTransportServiceIncidentsFeedManager(
            websession,
            self._generate_entity,
            self._update_entity,
            self._remove_entity,
            coordinates,
            filter_radius=radius_in_km,
            filter_categories=categories,
            hazard=hazard,
        )
        self._async_add_entities = async_add_entities
        self._scan_interval = scan_interval
        self._track_time_remove_callback = None
        self._hazard = hazard

    async def async_init(self):
        """Schedule initial and regular updates based on configured time interval."""

        async def update(event_time):
            """Update."""
            await self.async_update()

        # Trigger updates at regular intervals.
        self._track_time_remove_callback = async_track_time_interval(
            self._hass, update, self._scan_interval
        )

        _LOGGER.debug("Feed entity manager initialized")

    async def async_update(self):
        """Refresh data."""
        await self._feed_manager.update()
        _LOGGER.debug("Feed entity manager updated")

    async def async_stop(self):
        """Stop this feed entity manager from refreshing."""
        if self._track_time_remove_callback:
            self._track_time_remove_callback()
        _LOGGER.debug("Feed entity manager stopped")

    def get_entry(self, external_id):
        """Get feed entry by external id."""
        return self._feed_manager.feed_entries.get(external_id)

    async def _generate_entity(self, external_id):
        """Generate new entity."""
        new_entity = NswTransportServiceLocationEvent(self, external_id)
        # Add new entities to HA.
        self._async_add_entities([new_entity], True)

    async def _update_entity(self, external_id):
        """Update entity."""
        async_dispatcher_send(self._hass, SIGNAL_UPDATE_ENTITY.format(external_id))

    async def _remove_entity(self, external_id):
        """Remove entity."""
        async_dispatcher_send(self._hass, SIGNAL_DELETE_ENTITY.format(external_id))


class NswTransportServiceLocationEvent(GeolocationEvent):
    """This represents an external event with NSW Transport data."""

    def __init__(self, feed_manager, external_id):
        """Initialize entity with data from feed entry."""
        self._feed_manager = feed_manager
        self._external_id = external_id
        self._name = None
        self._distance = None
        self._latitude = None
        self._longitude = None
        self._attribution = None
        self._title = None
        self._category = None
        self._publication_date = None
        self._description = None
        # self._otherAdvice = None
        self._publicTransport = None
        self._adviceA = None
        self._adviceB = None
        self._adviceOther = None
        self._isMajor = None
        self._isEnded = None
        self._isNew = None
        self._isImpactNetwork = None
        self._diversions = None
        self._type = None
        self._subCategory = None
        self._duration = None
        self._road = None
        self._council_area = None
        self._hazard = feed_manager._hazard
        self._picture = None

    async def async_added_to_hass(self):
        """Call when entity is added to hass."""
        self._remove_signal_delete = async_dispatcher_connect(
            self.hass,
            SIGNAL_DELETE_ENTITY.format(self._external_id),
            self._delete_callback,
        )
        self._remove_signal_update = async_dispatcher_connect(
            self.hass,
            SIGNAL_UPDATE_ENTITY.format(self._external_id),
            self._update_callback,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Call when entity will be removed from hass."""
        self._remove_signal_delete()
        self._remove_signal_update()

    @callback
    def _delete_callback(self):
        """Remove this entity."""
        self.hass.async_create_task(self.async_remove())

    @callback
    def _update_callback(self):
        """Call update method."""
        self.async_schedule_update_ha_state(True)

    @property
    def should_poll(self):
        """No polling needed for NSW Transport location events."""
        return False

    async def async_update(self):
        """Update this entity from the data held in the feed manager."""
        _LOGGER.debug("Updating %s", self._external_id)
        feed_entry = self._feed_manager.get_entry(self._external_id)
        if feed_entry:
            self._update_from_feed(feed_entry)

    def _update_from_feed(self, feed_entry):
        """Update the internal state from the provided feed entry."""
        self._attribution = feed_entry.attribution
        self._name = feed_entry.title
        self._category = feed_entry.category
        self._external_id = feed_entry.external_id
        self._publication_date = feed_entry.publication_date
        self._description = feed_entry.description
        self._otherAdvice = re.sub("<[^<]+?>", "", feed_entry.otherAdvice)
        self._publicTransport = feed_entry.publicTransport
        self._adviceA = feed_entry.adviceA
        self._adviceB = feed_entry.adviceB
        self._adviceOther = re.sub("<[^<]+?>", "", feed_entry.adviceOther)
        self._isMajor = feed_entry.isMajor
        self._isEnded = feed_entry.isEnded
        self._isNew = feed_entry.isNew
        self._isImpactNetwork = feed_entry.isImpactNetwork
        self._diversions = re.sub("<[^<]+?>", "", feed_entry.diversions)
        self._type = feed_entry.type
        self._subCategory = feed_entry.subCategory
        self._duration = feed_entry.duration
        self._feature_type = feed_entry.feature_type
        self._road = feed_entry.road
        self._council_area = feed_entry.council_area
        self._picture = self.picture

        self._distance = feed_entry.distance_to_home
        self._latitude = feed_entry.coordinates[0]
        self._longitude = feed_entry.coordinates[1]

        # self._category = feed_entry.category
        # self._publication_date = feed_entry.publication_date
        # self._location = feed_entry.location
        # self._council_area = feed_entry.council_area
        # self._status = feed_entry.status
        # self._type = feed_entry.type
        # self._fire = feed_entry.fire
        # self._size = feed_entry.size
        # self._responsible_agency = feed_entry.responsible_agency

    @property
    def icon(self):
        """Return the icon to use in the frontend."""
        # Incidents :  mdi-alert
        # Fire : mdi-fire
        # Flood : mdi-home-flood
        # Alpine : mdi-snowflake-alert
        # Major events : mdi-party-popper
        # Roadworks : mdi-road-variant

        if self._hazard.lower().startswith("incident"):
            return "mdi:alert"
        if self._hazard.lower().startswith("fire"):
            return "mdi:fire"
        if self._hazard.lower().startswith("flood"):
            return "mdi:home-flood"
        if self._hazard.lower().startswith("alpine"):
            return "mdi:snowflake-alert"
        if self._hazard.lower().startswith("major"):
            return "mdi:party-popper"
        if self._hazard.lower().startswith("roadwork"):
            return "mdi:road-variant"
        return "mdi:alarm-light"

    @property
    def picture(self):
        """Return the picture to use in the frontend."""
        if self._hazard.lower().startswith("incident"):
            return (
                "https://www.livetraffic.com/images/icons/hazard/traffic-incident.gif"
            )
            # return "https://img.icons8.com/color/high-risk"
        if self._hazard.lower().startswith("fire"):
            return (
                "https://www.livetraffic.com/images/icons/hazard/weather-bush-fire.gif"
            )
            # return "https://img.icons8.com/color/fire-hazard"
        if self._hazard.lower().startswith("flood"):
            return "https://www.livetraffic.com/images/icons/hazard/weather-flood.gif"
            # return "https://img.icons8.com/color/floods"
        if self._hazard.lower().startswith("alpine"):
            return (
                "https://www.livetraffic.com/images/icons/hazard/weather-snow-ice.gif"
            )
            # return "https://img.icons8.com/color/snowflake"
        if self._hazard.lower().startswith("major"):
            return "https://www.livetraffic.com/images/icons/hazard/major-event.gif"
            # return "https://img.icons8.com/color/star"
        if self._hazard.lower().startswith("roadwork"):
            return "https://www.livetraffic.com/images/icons/hazard/road-work-3.png"
            # return "https://img.icons8.com/color/under-construction"
        return None

    @property
    def source(self) -> str:
        """Return source value of this external event."""
        return SOURCE

    @property
    def name(self) -> Optional[str]:
        """Return the name of the entity."""
        return self._name

    @property
    def distance(self) -> Optional[float]:
        """Return distance value of this external event."""
        return self._distance

    @property
    def latitude(self) -> Optional[float]:
        """Return latitude value of this external event."""
        return self._latitude

    @property
    def longitude(self) -> Optional[float]:
        """Return longitude value of this external event."""
        return self._longitude

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return DEFAULT_UNIT_OF_MEASUREMENT

    @property
    def device_state_attributes(self):
        """Return the device state attributes."""
        attributes = {}
        for key, value in (
            (ATTR_TITLE, self._title),
            (ATTR_FEATURE_TYPE, self._hazard),
            (ATTR_TYPE, self._type),
            (ATTR_SUBCATEGORY, self._subCategory),
            (ATTR_ROAD, self._road),
            (ATTR_COUNCIL, self._council_area),
            (ATTR_CATEGORY, self._category),
            (ATTR_DESCRIPTION, self._description),
            (ATTR_PUBLICATION_DATE, self._publication_date),
            (ATTR_ADVICEA, self._adviceA),
            (ATTR_ADVICEB, self._adviceB),
            (ATTR_DIVERSIONS, self._diversions),
            (ATTR_PUBLICTRANSPORT, self._publicTransport),
            (ATTR_ADVICEOTHER, self._adviceOther),
            (ATTR_ISMAJOR, self._isMajor),
            (ATTR_ISENDED, self._isEnded),
            (ATTR_ISNEW, self._isNew),
            (ATTR_ISIMPACTNETWORK, self._isImpactNetwork),
            (ATTR_DURATION, self._duration),
            (ATTR_EXTERNAL_ID, self._external_id),
            (ATTR_ATTRIBUTION, self._attribution),
            (ATTR_PICTURE, self._picture),
        ):
            if value or isinstance(value, bool):
                attributes[key] = value
        return attributes

    def cleanuphtml(self, prop):
        """Clean string from HTML tags."""
        return re.sub("<[^<]+?>", "", prop)
