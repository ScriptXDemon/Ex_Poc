"""Curated promotion of A14 ontology proposals (corpus1) into the parameter catalogue.

Every proposal gets one decision:
  promote -> a new catalogue parameter (status: candidate)
  merge   -> its page labels become aliases of an existing or newly promoted parameter
  reject  -> not a product parameter (entity / procurement / narrative / generic), or a mapping artefact that a
             parser / mapping fix removes (the reason says which)

Writes src/defence_extractor/ontology/extensions/a14_corpus1.yaml (loaded on top of seed_v1.yaml) and
docs/ontology_a14_review.md (the decision for every proposal, with its evidence counts and examples).
usage: python3 scripts/ontology_promote_a14.py runs/corpus1/ontology_proposals.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "src/defence_extractor/ontology/extensions/a14_corpus1.yaml"
REVIEW = ROOT / "docs/ontology_a14_review.md"

ALL = ["*"]
VEH = ["armoured_vehicle", "tank", "apc_ifv", "mrap", "light_tactical_vehicle", "logistics_truck", "ugv", "engineering_vehicle", "artillery"]
ARMS = ["small_arm", "sniper_rifle", "machine_gun", "grenade_launcher"]
OPTICS = ["eo_ir", "night_vision", "laser_rangefinder", "small_arm", "sniper_rifle", "machine_gun", "turret_rws"]
ELEC = ["radar", "eo_ir", "sonar", "laser_rangefinder", "night_vision", "acoustic_sensor", "electronic_warfare", "communication_c2",
        "antenna", "navigation", "computing_rugged", "power_system", "battery", "uav", "ugv", "usv", "uuv", "satellite", "ground_segment",
        "component_subsystem", "simulation_training", "support_equipment"]
COMMS = ["communication_c2", "antenna", "electronic_warfare", "radar", "uav", "ugv", "usv", "uuv", "satellite", "ground_segment", "navigation", "computing_rugged"]
NAV = ["navigation", "uav", "ugv", "usv", "uuv", "loitering_munition", "missile", "combat_aircraft", "helicopter", "computing_rugged"]
AMMO = ["ammunition", "small_arm", "sniper_rifle", "machine_gun", "gun_cannon", "artillery", "mortar", "grenade_launcher", "rocket", "missile", "mine", "torpedo", "bomb_guidance_kit"]
PRESS = ["component_subsystem", "launch_vehicle", "satellite", "missile", "rocket", "torpedo", "support_equipment"]
AIR = ["combat_aircraft", "trainer_aircraft", "transport_aircraft", "special_mission_aircraft", "helicopter", "uav", "loitering_munition", "aerostat", "missile", "launch_vehicle"]
SHIP = ["surface_combatant", "patrol_vessel", "auxiliary_vessel"]


def P(id, name, family, aliases, desc, dimension=None, classes=ALL, multi=False, type_=None):
    d = {"id": id, "name": name, "family": family, "status": "candidate", "classes": classes, "aliases": aliases, "description": desc}
    if dimension:
        d["dimension"] = dimension
    if type_:
        d["type"] = type_
    if multi:
        d["multi"] = True
    return d


NEW = [
    # identity / support
    P("part_number", "Part / Order Number", "identity", ["part number", "part no", "p/n", "product number", "product no", "order number",
      "order no", "order code", "article number", "catalogue number", "catalog number", "item number", "reference number"],
      "Manufacturer part, order or catalogue number of the item (not a contract number)."),
    P("nsn", "NATO Stock Number", "identity", ["nsn", "nato stock number", "national stock number", "product nsn"], "NATO / national stock number."),
    P("material", "Material", "physical", ["material", "materials", "material composition", "construction material", "body material",
      "case material", "housing material", "frame material", "shell material", "penetrator material"],
      "Main material(s) of the item or a named part (e.g. 'C103 niobium alloy', 'polymer frame')."),
    P("colour", "Colour / Finish", "physical", ["color", "colour", "colors", "colours", "available colors", "available colours", "finish",
      "barrel finish", "surface finish", "coating colour"], "Colour, camouflage or surface finish options."),
    P("dimensions", "Dimensions (L x W x H)", "physical", ["dimensions", "overall dimensions", "external dimensions", "size", "l x w x h",
      "lxwxh", "dimensions lxwxh", "fuselage dimensions", "car size", "envelope dimensions"],
      "Composite dimensions as stated; also decomposed into length / width / height when they are an L x W x H triple.", dimension="length"),
    P("length_folded", "Length Folded / Retracted", "physical", ["length folded", "folded length", "total length folded", "length retracted",
      "retracted length", "total length retracted", "length with stock folded", "transport length"],
      "Length with stock, rotor, wings or mast folded / retracted.", dimension="length"),
    P("hull_height", "Hull Height", "physical", ["hull height", "height over hull", "height hull", "height to hull top"],
      "Height to the top of the hull (excluding turret / weapon station).", dimension="length", classes=VEH),
    P("volume", "Volume", "physical", ["volume", "internal volume", "cargo volume", "cargo bay volume", "stowage volume"],
      "Internal / cargo volume.", dimension="volume"),
    P("display_size", "Display Size", "other_technical", ["display size", "screen size", "display", "monitor size"],
      "Diagonal size of the display / screen.", dimension="length", classes=ELEC),
    # environment & standards
    P("operating_environment", "Operating Environment", "operating_environment", ["operating environment", "operational environment",
      "environmental conditions", "operating conditions", "operational conditions", "operating condition",
      "operational condition", "climate", "weather capability", "all-weather capability"],
      "Environments / conditions the item is designed for (all-weather, high altitude, desert, maritime...).", multi=True),
    P("storage_temperature", "Storage Temperature", "operating_environment", ["storage temperature", "storage temp", "storage temperature range",
      "temperature limits storage", "non-operating temperature"], "Storage (non-operating) temperature range.", dimension="temperature"),
    P("humidity", "Humidity", "operating_environment", ["humidity", "relative humidity", "operating humidity", "rh"],
      "Relative humidity the item withstands.", dimension="ratio"),
    P("max_wind_speed", "Maximum Wind Speed", "operating_environment", ["max wind speed", "maximum wind speed", "wind resistance",
      "max wind speed resistance", "maximum wind speed resistance", "wind speed limit", "max wind", "max wind peak", "max wind conditions"],
      "Maximum wind speed for operation / launch / deployment.", dimension="speed"),
    P("standard_compliance", "Standards Compliance", "other_technical", ["standard compliance", "compliance standard",
      "conforms to", "military standard compliance", "regulatory compliance", "qualification standard",
      "qualification basis standard", "design standard"], "Standards the item complies with or is qualified to (STANAG, MIL-STD, EN, ICAO...).",
      multi=True),
    P("environmental_standard", "Environmental Qualification", "operating_environment", ["environmental standard", "environmental",
      "environmental qualification", "environmental testing", "environmental specification", "vibration", "shock and vibration",
      "vibration standard"], "Environmental qualification standard (e.g. MIL-STD-810, IEC 60945).", multi=True),
    P("emc_standard", "EMC / EMI Compliance", "other_technical", ["emc", "emi", "emc compliance", "emi compliance",
      "electromagnetic compatibility", "emc/emi"], "Electromagnetic compatibility / interference standard (e.g. MIL-STD-461).", multi=True),
    P("safety_standard", "Safety Standard", "other_technical", ["safety standard", "product safety", "laser safety standard",
      "insensitive munition", "im compliance", "safety certification"], "Safety standard or safety classification (e.g. insensitive munition).",
      multi=True),
    P("certification", "Certification", "other_technical", ["certification", "certifications", "approval", "approvals",
      "type certification", "type approval"], "Certifications / approvals held (e.g. AS9100, FAR 25, MID).", multi=True),
    P("vibration_load", "Vibration Load", "operating_environment", ["environmental load", "vibration load", "random vibration",
      "environmental load axial", "environmental load lateral"], "Random vibration level the item is qualified to (Grms), per axis as a condition."),
    # pressure vessels (tanks, COPVs, valves)
    P("operating_pressure", "Operating Pressure", "other_technical", ["operating pressure", "maximum expected operating pressure", "meop",
      "working pressure", "max operating pressure", "rated pressure"], "Operating / working pressure.", dimension="pressure", classes=PRESS),
    P("burst_pressure", "Burst Pressure", "other_technical", ["burst pressure", "rupture pressure", "actual rupture pressure",
      "minimum burst pressure", "design burst pressure"], "Burst / rupture pressure.", dimension="pressure", classes=PRESS),
    P("proof_factor", "Proof Pressure Factor", "other_technical", ["proof factor", "proof pressure factor"], "Proof pressure / MEOP factor.",
      classes=PRESS),
    P("burst_factor", "Burst Factor", "other_technical", ["burst factor", "minimum burst factor", "burst safety factor"],
      "Burst pressure / MEOP safety factor.", classes=PRESS),
    # electrical
    P("operating_voltage", "Operating Voltage", "other_technical", ["operating voltage", "operating voltage range", "supply voltage",
      "power supply", "input voltage", "voltage", "nominal voltage", "electrical system", "electrical system voltage", "dc input",
      "external power supply"], "Supply / operating voltage (range) of the item.", dimension="voltage"),
    P("output_current", "Output Current", "other_technical", ["output current", "max output current", "rated current"], "Output current.",
      dimension="current", classes=ELEC),
    P("power_consumption", "Power Consumption", "other_technical", ["power consumption", "power draw", "power requirement",
      "power input", "maximum input power", "input power"], "Electrical power consumed by the item.", dimension="power"),
    P("cooling_type", "Cooling", "other_technical", ["cooling", "cooling type", "cooling concept", "cooling system", "cooling method"],
      "Cooling method (air-cooled, liquid-cooled, forced air...)."),
    # communications / computing
    P("interfaces", "Interfaces", "communication_c2", ["interface", "interfaces", "data interface", "data interfaces", "communication interface",
      "communication interfaces", "communication ports", "i/o", "connectivity", "onboard connectivity", "interface type", "output interfaces",
      "ports", "bus interface"], "Data / communication / control interfaces (Ethernet, CAN, RS-422, MIL-STD-1553...).", multi=True),
    P("connector_type", "Connector Type", "communication_c2", ["connector", "connector type", "rf connector", "power connector",
      "ethernet/power connector"], "Physical connector type(s).", classes=ELEC),
    P("encryption", "Encryption", "communication_c2", ["encryption", "crypto", "encryption standard", "comsec"], "Encryption algorithms / standards.",
      classes=COMMS),
    P("modulation", "Modulation", "communication_c2", ["modulation", "modulation type", "waveform", "waveforms"], "Modulation scheme / waveform.",
      classes=COMMS),
    P("channel_bandwidth", "Channel Bandwidth", "communication_c2", ["channel bandwidth", "bandwidth", "fm bandwidth", "instantaneous bandwidth"],
      "Channel / instantaneous bandwidth.", dimension="frequency", classes=COMMS),
    P("antenna_gain", "Antenna Gain", "communication_c2", ["antenna gain", "gain"], "Antenna gain (dBi / dBic).", dimension="level",
      classes=COMMS),
    P("eirp", "EIRP", "communication_c2", ["eirp", "equivalent isotropic radiated power", "effective isotropic radiated power"],
      "Equivalent isotropic radiated power.", dimension="level", classes=COMMS),
    P("impedance", "Impedance", "communication_c2", ["impedance", "nominal impedance"], "Electrical impedance (ohm).", classes=ELEC),
    P("data_link_range", "Data Link Range", "communication_c2", ["data link range", "link range", "communication range", "transmission range",
      "datalink range", "control range"], "Range of the data / control link.", dimension="length", classes=COMMS + ["loitering_munition"]),
    P("update_rate", "Update Rate", "performance", ["update rate", "refresh rate", "output rate", "data update rate"], "Update / refresh rate.",
      classes=ELEC),
    P("storage_capacity", "Storage Capacity", "other_technical", ["storage capacity", "local storage", "ssd", "memory storage"],
      "Data storage capacity.", classes=["computing_rugged", "communication_c2", "uav", "ugv", "navigation", "eo_ir", "simulation_training"]),
    P("memory_capacity", "Memory (RAM)", "other_technical", ["ram", "memory", "ram capacity", "system memory"], "Working memory (RAM).",
      classes=["computing_rugged", "communication_c2", "navigation", "simulation_training"]),
    P("software", "Software", "other_technical", ["software", "firmware", "software platform", "pre-installed application"],
      "Software / firmware supplied with the item.", classes=ELEC),
    # sensors / optics / navigation
    P("magnification", "Magnification / Zoom", "sensor", ["magnification", "zoom", "optical zoom", "digital zoom", "zoom factor",
      "magnifier"], "Optical / digital magnification or zoom range (e.g. '3-12x').", classes=OPTICS),
    P("objective_lens_diameter", "Objective Lens Diameter", "sensor", ["objective lens diameter", "objective diameter", "objective lens",
      "objective"], "Objective lens diameter of a sight / scope.", dimension="length", classes=OPTICS),
    P("field_of_view", "Field of View", "sensor", ["field of view", "fov", "angle of view", "field of view (h x v)", "viewing angle"],
      "Field of view.", dimension="angle", classes=OPTICS + ["radar", "uav", "ugv", "acoustic_sensor"]),
    P("wavelength", "Wavelength", "sensor", ["wavelength", "laser wavelength", "operating wavelength", "wave length"], "Operating wavelength.",
      dimension="length", classes=["laser_rangefinder", "eo_ir", "night_vision", "directed_energy", "small_arm"]),
    P("beam_divergence", "Beam Divergence", "sensor", ["beam divergence", "divergence"], "Laser beam divergence (mrad).",
      classes=["laser_rangefinder", "eo_ir", "directed_energy", "small_arm"]),
    P("laser_safety_class", "Laser Safety Class", "sensor", ["laser class", "laser safety class", "eye safety class", "laser classification"],
      "Laser safety class (1, 3R, 3B...).", classes=["laser_rangefinder", "eo_ir", "night_vision", "small_arm", "directed_energy"]),
    P("detector_type", "Detector / Imager Type", "sensor", ["detector", "detector type", "imager type", "thermal imager", "sensor type",
      "focal plane array", "fpa"], "Detector / imager technology (uncooled VOx microbolometer, MWIR...).", classes=OPTICS + ["uav", "ugv"]),
    P("sight_type", "Sight Type", "fire_control", ["sight type", "scope type", "optic type", "optics", "type of sight"],
      "Type of sight / optic fitted or offered.", classes=ARMS + ["sniper_rifle", "turret_rws", "eo_ir", "night_vision"]),
    P("mount_ring_diameter", "Mount Ring / Tube Diameter", "sensor", ["ring size", "ring diameter", "tube diameter", "main tube diameter"],
      "Scope tube / mounting ring diameter.", dimension="length", classes=OPTICS),
    P("azimuth_coverage", "Azimuth Coverage", "sensor", ["azimuth coverage", "coverage azimuth", "horizontal coverage",
      "antenna coverage", "coverage"], "Azimuth coverage of a sensor / antenna.", dimension="angle",
      classes=["radar", "eo_ir", "sonar", "electronic_warfare", "antenna", "acoustic_sensor", "air_defence_system"]),
    P("position_accuracy", "Position Accuracy", "guidance_navigation", ["position accuracy", "horizontal position accuracy", "positioning accuracy",
      "navigation accuracy", "horizontal accuracy"], "Navigation / positioning accuracy (per mode as condition).", dimension="length", classes=NAV),
    P("heading_accuracy", "Heading Accuracy", "guidance_navigation", ["heading accuracy", "heading"], "Heading accuracy.", dimension="angle",
      classes=NAV),
    P("attitude_accuracy", "Roll / Pitch Accuracy", "guidance_navigation", ["roll/pitch accuracy", "pitch & roll accuracy", "pitch roll accuracy",
      "roll pitch accuracy", "attitude accuracy", "roll/pitch", "pitch roll"], "Roll / pitch (attitude) accuracy.", dimension="angle", classes=NAV),
    P("velocity_accuracy", "Velocity Accuracy", "guidance_navigation", ["velocity accuracy", "horizontal velocity accuracy",
      "vertical velocity accuracy"], "Velocity accuracy.", dimension="speed", classes=NAV),
    # weapons / ammunition
    P("rifling", "Rifling", "armament", ["rifling", "grooves", "rifling grooves", "number of grooves"],
      "Rifling (grooves, direction); the twist rate itself is twist_rate.", classes=ARMS + ["sniper_rifle", "gun_cannon"]),
    P("fire_modes", "Fire Modes", "armament", ["fire mode", "fire modes", "firing mode", "firing modes", "modes of fire", "selector",
      "operation modes (weapon)"], "Available fire modes (safe / semi / burst / auto).", multi=True, classes=ARMS + ["gun_cannon", "turret_rws"]),
    P("accessory_rails", "Accessory Rails", "armament", ["rails", "accessory rails", "rail interface", "picatinny", "m-lok", "keymod"],
      "Accessory rail interfaces (Picatinny, M-LOK...).", classes=ARMS + ["sniper_rifle"]),
    P("cartridge_weight", "Cartridge Weight", "ammunition", ["cartridge weight", "round weight", "complete round weight", "weight of round",
      "nominal wt", "patronengewicht"], "Weight of the complete cartridge / round (projectile weight is projectile_weight).",
      dimension="mass", classes=AMMO),
    P("cartridge_length", "Cartridge Length", "ammunition", ["cartridge length", "overall cartridge length", "round length", "coal",
      "patronenlänge", "nominal length"], "Overall length of the complete cartridge / round.", dimension="length", classes=AMMO),
    P("propellant_type", "Propellant Type", "ammunition", ["propellant", "propellant type", "powder", "propellant powder"],
      "Propellant type / designation.", classes=AMMO + ["launch_vehicle", "satellite"]),
    P("warhead_effect", "Warhead Effect", "armament", ["warhead effect", "warhead effects", "terminal effect"],
      "Effect of the warhead (blast, fragmentation, incendiary...).", multi=True, classes=AMMO + ["loitering_munition"]),
    P("initiation_mode", "Initiation Mode", "ammunition", ["initiation mode", "initiation method", "fuze mode", "function mode",
      "initiation"], "How the munition is initiated / its fuze modes (delay, command, impact...).", classes=AMMO),
    P("detonation_delay", "Detonation Delay", "ammunition", ["detonation delay", "delay time", "functioning delay"], "Detonation / function delay.",
      dimension="time", classes=AMMO),
    P("standoff_distance", "Standoff Distance", "ammunition", ["standoff distance", "stand-off distance", "standoff"],
      "Standoff distance of a charge or munition effect.", dimension="length", classes=AMMO + ["engineering_vehicle", "armour_protection_kit"]),
    P("launcher_configuration", "Launcher Configuration", "armament", ["launcher configuration", "launch configuration",
      "pod configuration", "launching pod"], "Launcher / pod arrangement (e.g. '2 quad launchers').",
      classes=["missile", "rocket", "launcher", "air_defence_system", "surface_combatant", "patrol_vessel", "artillery"]),
    P("launch_method", "Launch Method", "mission_target", ["launch method", "launch type", "launch mode"],
      "How the item is launched (canister, rail, catapult, drop, vertical cold launch...).",
      classes=["missile", "rocket", "uav", "loitering_munition", "torpedo", "launch_vehicle", "launcher"]),
    # mobility / vehicles
    P("approach_angle", "Approach Angle", "mobility", ["approach angle", "angle of approach"], "Approach angle.", dimension="angle", classes=VEH),
    P("departure_angle", "Departure Angle", "mobility", ["departure angle", "angle of departure"], "Departure angle.", dimension="angle",
      classes=VEH),
    P("track_width", "Track Width", "mobility", ["track width", "wheel track", "wheel track width", "track (wheel)"],
      "Distance between wheel centrelines on one axle.", dimension="length", classes=VEH),
    P("track_gauge", "Rail Track Gauge", "mobility", ["track gauge", "rail gauge", "broad gauge", "standard gauge"],
      "Rail track gauge (rail vehicles).", dimension="length"),
    P("tyre_size", "Tyre Size", "mobility", ["tyres", "tyre", "tires", "tire", "tyre size", "tire size", "wheels and tyres"],
      "Tyre designation / size.", classes=VEH + ["transport_aircraft", "uav"]),
    P("steering", "Steering", "mobility", ["steering", "steering system", "steering type"], "Steering system.", classes=VEH),
    P("engine_displacement", "Engine Displacement", "propulsion", ["engine displacement", "cubic capacity", "swept volume", "engine capacity"], "Engine swept volume (litres / cc).", dimension="volume", classes=VEH + ["uav", "patrol_vessel", "usv"]),
    P("engine_speed", "Engine Speed", "propulsion", ["engine speed", "rated speed", "engine speed range", "rated engine speed", "rpm"],
      "Engine (rated) speed.", dimension="rate"),
    P("fuel_type", "Fuel Type", "propulsion", ["fuel", "fuel type", "fuel grade", "fuels"], "Fuel type(s) the item runs on.", multi=True),
    P("thrust", "Thrust", "propulsion", ["thrust", "max thrust", "maximum thrust", "thrust range", "engine thrust", "static thrust",
      "take-off thrust"], "Engine / motor thrust.", dimension="force"),
    # aviation / naval / launch
    P("launch_altitude", "Launch Altitude", "performance", ["launch altitude", "release altitude", "drop altitude"],
      "Altitude at which the item is launched / released.", dimension="length", classes=AIR + ["launcher"]),
    P("turnaround_time", "Turnaround Time", "maintainability_support", ["turnaround time", "combat turnaround", "combat turnaround time",
      "rearm time", "refuel and rearm time", "turn-around time"], "Time to turn the platform around between missions.", dimension="time",
      classes=AIR + ["artillery", "launcher", "air_defence_system"]),
    P("aircraft_capacity", "Aircraft Capacity", "payload", ["aircraft capacity", "embarked aircraft", "air wing", "air group", "hangar capacity",
      "aircraft carried"], "Number / type of aircraft a ship carries or operates.", classes=SHIP + ["surface_combatant"]),
    P("mounting_type", "Mounting", "compatibility", ["mounting", "mounting type", "mount type", "antenna mount"],
      "How the item is mounted / installed."),
    # performance / support
    P("response_time", "Response Time", "performance", ["response time", "reaction time", "system reaction time"], "Response / reaction time.",
      dimension="time"),
    P("reliability", "Reliability", "maintainability_support", ["reliability", "mission reliability", "launch reliability", "dispatch reliability"],
      "Reliability figure (probability / rate)."),
    P("shelf_life", "Shelf Life", "maintainability_support", ["shelf life", "storage life", "shelf life minimum"], "Shelf / storage life.",
      dimension="time"),
    P("service_life", "Service Life", "maintainability_support", ["service life", "design life", "operational life", "life time", "lifetime",
      "useful life"], "Design / service life.", dimension="time"),
    P("packaging", "Packaging", "maintainability_support", ["packaging", "packing", "packaging configuration"],
      "Packaging / packing configuration (e.g. '20 cartridges per box').", classes=AMMO + ["support_equipment", "component_subsystem"]),
    P("operating_modes", "Operating Modes", "automation_autonomy", ["operating mode", "operating modes", "operational mode", "operational modes",
      "mode of operation", "modes of operation", "typical operational mode"],
      "Operating modes (autonomous, remote-controlled, active / passive...).", multi=True),
]

# label aliases added to parameters that already exist in seed_v1
ALIAS_ADD = {
    "length_overall": ["total length extended", "length extended", "length (stock extended)", "total length (extended)", "length mm"],
    "operating_temperature": ["operating temperature range", "temperature range", "operating temp", "temperature limits operating"],
    "diameter": ["diameter mm"],
    "torque": ["max torque", "maximum torque", "engine torque"],
    "max_range": ["maximum range (km)"],
}
ALIAS_REMOVE = {
    "projectile_weight": ["round weight", "cartridge weight"],  # a cartridge is not its projectile -> cartridge_weight
    "power_output": ["power consumption"],                      # consumption is not output -> power_consumption
    "twist_rate": ["rifling"],                                  # "6 RH grooves" is rifling; the twist rate is "1:7 in"
    "deployment_time": ["reaction time"],                       # reaction to a threat is not time into action -> response_time
}

# carried-over page labels that contradict their target (they belong elsewhere)
BLOCK = {"length_overall": ("retract", "fold"), "operating_temperature": ("insertion loss", "storage", "heating"),
         "operating_voltage": ("interface", "connectivity"), "power_consumption": ("connectivity",),
         "field_of_view": ("camera", "lens"), "display_size": ("interface", "geräte"), "velocity_accuracy": ("horizontal velocity", "vertical velocity")}

# page labels never carried over as aliases: too generic, or names of components / other quantities
DROP_LABELS = {"penetrator", "color_difference", "vcc color", "gnd color", "operating environment temperature", "environment", "storage",
               "available storage space", "wind", "standard", "emi", "emc mains filter (active pfc)", "safety", "pressure", "power",
               "mechanical interface tolerance", "human machine interface type", "mobile storage cage load capacity",
               "nvg-compatible laser pointer", "laser rangefinder", "laser pointer", "size", "position", "type", "warhead", "launcher",
               "launchers", "track", "tread", "d — track", "gauge", "thrust profile", "thrust contribution", "service",
               "service life end year", "operating", "modes", "ais unit input voltage", "input voltage (output voltage)"}

# proposal id -> (decision, target parameter or None, reason)
M, R = "merge", "reject"
DEC = {
    # ---- merged into a promoted or existing parameter ----
    "operational_environment": (M, "operating_environment", ""), "operational_condition": (M, "operating_environment", ""),
    "color": (M, "colour", ""), "finish": (M, "colour", ""),
    "operational_mode": (M, "operating_modes", ""), "operating_mode": (M, "operating_modes", ""),
    "power_supply": (M, "operating_voltage", ""), "input_voltage": (M, "operating_voltage", ""), "voltage": (M, "operating_voltage", ""),
    "supply_voltage": (M, "operating_voltage", ""), "electrical_system_voltage": (M, "operating_voltage", ""),
    "interface": (M, "interfaces", ""), "data_interface": (M, "interfaces", ""), "interface_type": (M, "interfaces", ""),
    "input_power_factor": (M, "power_consumption", "labels were 'Power Input' / 'Maximum Input Power'"),
    "zoom": (M, "magnification", ""), "thermal_imager_type": (M, "detector_type", ""),
    "firing_mode": (M, "fire_modes", ""), "fire_mode": (M, "fire_modes", ""), "rail_configuration": (M, "accessory_rails", ""),
    "wind_resistance": (M, "max_wind_speed", ""), "initiation_method": (M, "initiation_mode", ""),
    "cooling_concept": (M, "cooling_type", ""), "regulatory_compliance": (M, "standard_compliance", ""),
    "qualification_standard": (M, "standard_compliance", ""), "vibration_standard": (M, "environmental_standard", ""),
    "emc_compliance": (M, "emc_standard", ""), "horizontal_position_accuracy": (M, "position_accuracy", ""),
    "roll_pitch_accuracy": (M, "attitude_accuracy", ""), "ram_capacity": (M, "memory_capacity", ""),
    "modulation_type": (M, "modulation", ""), "ring_size": (M, "mount_ring_diameter", ""), "height_over_hull": (M, "hull_height", ""),
    "steering_system": (M, "steering", ""), "pod_configuration": (M, "launcher_configuration", ""),
    "environmental_load_axial": (M, "vibration_load", "axis kept as a condition"), "environmental_load_lateral": (M, "vibration_load", "axis kept as a condition"),
    "actual_rupture_pressure": (M, "burst_pressure", ""), "minimum_burst_factor": (M, "burst_factor", ""),
    "warhead_effects": (M, "warhead_effect", ""), "total_length_folded": (M, "length_folded", ""),
    "total_length_extended": (M, "length_overall", "the stock-extended length is the overall length"),
    "total_length": (M, "length_overall", "labels 'Total length (mm)' / 'Length mm': the unit in the header is now read (mapping fix)"),
    "operating_temperature_range": (M, "operating_temperature", ""), "diameter_mm": (M, "diameter", ""),
    # ---- same-name promotions ----
    **{k: ("promote", k, "") for k in [
        "material", "dimensions", "shelf_life", "standard_compliance", "storage_temperature", "magnification", "safety_standard",
        "operating_voltage", "interfaces", "service_life", "launch_method", "thrust", "environmental_standard", "wavelength", "packaging",
        "certification", "humidity", "volume", "fuel_type", "operating_pressure", "approach_angle", "departure_angle", "reliability",
        "rifling", "standoff_distance", "proof_factor", "objective_lens_diameter", "cartridge_weight", "track_gauge", "sight_type",
        "field_of_view", "initiation_mode", "power_consumption", "propellant_type", "azimuth_coverage", "turnaround_time", "display_size",
        "cooling_type", "detonation_delay", "storage_capacity", "response_time", "connector_type", "track_width", "channel_bandwidth",
        "antenna_gain", "eirp", "cartridge_length", "heading_accuracy", "laser_safety_class", "beam_divergence", "velocity_accuracy",
        "output_current", "data_link_range", "encryption", "nsn", "mounting_type", "aircraft_capacity", "update_rate", "impedance",
        "engine_speed", "launcher_configuration", "max_wind_speed", "software", "launch_altitude", "tyre_size"]},
    # ---- rejected: mapping artefacts (a parser / mapping fix removes them) ----
    **{k: (R, None, "mapping artefact: value did not fit the existing parameter (qualitative value, unit in the table header, or the "
                    "'Nm' torque / 'KN' thrust unit bug, both fixed)") for k in [
        "weight_other", "operational_range_other", "protection_level_other", "product_type_other", "rate_of_fire_other",
        "payload_capacity_other", "max_speed_other", "power_output_other", "deployment_time_other", "calibre_other",
        "propulsion_type_other", "torque_other", "max_range_other", "endurance_other", "fuel_capacity_other", "engine_power_other",
        "displacement_other", "length_overall_other", "main_armament_other"]},
    # ---- rejected: not product parameters ----
    **{k: (R, None, "entity / relationship information, captured as entities and relations, not as a parameter") for k in [
        "manufacturer", "operator", "customer", "product_name", "related_product", "product_family", "variant", "variants",
        "variant_designation", "variant_count", "model", "derived_from", "role", "component", "engine"]},
    **{k: (R, None, "programme / procurement / status information, not a specification (rule 6)") for k in [
        "operational_status", "quantity", "production_location", "qualification_status", "manufacturing"]},
    **{k: (R, None, "generic or vague label; facts are kept as capabilities / features / missions text") for k in [
        "operational_characteristic", "configuration", "system_configuration", "deployment_configuration", "architecture",
        "feature", "capability", "description", "mission", "application", "flight_profile", "mobility_characteristic",
        "performance_claim", "design_basis", "design_reference", "operational_requirement", "armament", "mounting_location",
        "classification", "category", "generation", "beam_shape", "noise_level", "functional_safety"]},
    **{k: (R, None, "mixed / noisy cluster (labels from unrelated tables)") for k in [
        "uns_number", "value", "other", "h", "composition", "propellant_mass", "sensitivity", "wheelbase_to_axle_3", "dock_depth",
        "signal_processing_type", "output_power_stability", "dc_dc_converter_power", "motor_speed_control_range"]},
}


def main() -> None:
    props = json.loads(Path(sys.argv[1]).read_text())
    new_ids = {p["id"] for p in NEW}
    missing = [p["proposed_id"] for p in props if p["proposed_id"] not in DEC]
    if missing:
        raise SystemExit(f"no decision for: {missing}")
    # labels of merged proposals become aliases of their target
    import re

    def words(x: str) -> set[str]:
        return {w for w in re.split(r"[^a-z0-9]+", x.lower()) if len(w) > 2 and w not in {"the", "and", "max", "min", "other"}}

    names = {p["id"]: words(p["id"].replace("_", " ") + " " + p["name"] + " " + " ".join(p["aliases"])) for p in NEW}
    for k, v in ALIAS_ADD.items():
        names.setdefault(k, words(k.replace("_", " ") + " " + " ".join(v)))
    extra: dict[str, list[str]] = {}
    for p in props:
        d, target, _ = DEC[p["proposed_id"]]
        if d in ("merge", "promote") and target:
            for lab in p["source_labels"]:
                lab = (lab or "").lower().strip(" :")
                if any(b in lab for b in BLOCK.get(target, ())) or lab in DROP_LABELS:
                    continue
                if lab and len(lab) <= 60 and not any(c.isdigit() for c in lab) and words(lab) & names.get(target, set()):
                    extra.setdefault(target, []).append(lab)
    params = []
    for p in NEW:
        q = dict(p)
        q["aliases"] = list(dict.fromkeys([*p["aliases"], *[a for a in extra.get(p["id"], []) if a not in p["aliases"]]]))[:24]
        params.append(q)
    alias_add = {k: list(dict.fromkeys(v + [a for a in extra.get(k, []) if a not in v])) for k, v in ALIAS_ADD.items()}
    for k, v in extra.items():
        if k not in new_ids and k not in alias_add:
            alias_add[k] = list(dict.fromkeys(v))
    import yaml  # PyYAML (installed with the project)

    EXT.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Ontology extension v1.1.0: parameters promoted from the A14 proposals of corpus run corpus1 (459 documents),\n"
              "# curated 2026-10-09 (see docs/ontology_a14_review.md for every proposal and its decision).\n"
              "# Loaded on top of seed_v1.yaml by load_ontology(). New parameters enter as status 'candidate'.\n")
    body = {"version": "1.1.0", "source": "A14 proposals, run corpus1", "alias_additions": alias_add, "alias_removals": ALIAS_REMOVE,
            "parameters": params}
    EXT.write_text(header + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=140), encoding="utf-8")

    rows = []
    for p in props:
        d, target, why = DEC[p["proposed_id"]]
        ex = " · ".join(x[:40] for x in p["examples"][:3])
        labels = ", ".join(list(p["source_labels"])[:4])
        tgt = f"`{target}`" if target else ""
        rows.append(f"| `{p['proposed_id']}` | {p['documents']} | {p['occurrences']} | **{d}** {tgt} | {why} | {labels} | {ex} |")
    counts = {k: sum(1 for p in props if DEC[p["proposed_id"]][0] == k) for k in ("promote", "merge", "reject")}
    REVIEW.write_text(
        "# A14 proposals -> catalogue (corpus1, 2026-10-09)\n\n"
        f"{len(props)} proposals (dynamic properties seen in 3+ documents). Decisions: {counts['promote']} promoted as new parameters, "
        f"{counts['merge']} merged into a parameter as aliases, {counts['reject']} rejected. Together with a few parameters that group "
        f"several proposals, the catalogue grows from 92 to {92 + len(NEW)} parameters (new ones enter as `candidate`).\n\n"
        "Also changed: `projectile_weight` no longer treats 'round weight' / 'cartridge weight' as aliases (a cartridge is not its "
        "projectile; see `cartridge_weight`), and units 'Nm' (torque) / 'KN' (thrust) are no longer read as nautical miles / knots.\n\n"
        "| Proposal | Docs | Uses | Decision | Why | Page labels | Examples |\n|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n",
        encoding="utf-8")
    print(f"{len(NEW)} new parameters, alias additions on {len(alias_add)} parameters; decisions {counts} -> {EXT.relative_to(ROOT)}, {REVIEW.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
