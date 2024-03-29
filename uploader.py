from models import VampireSheet

import logging
from pprint import pprint, pformat
from datetime import timedelta, datetime

#from google.appengine.ext.db import BadValueError
from google.appengine.ext.db import IntegerProperty, BooleanProperty, FloatProperty

DEFAULT_DATE_HINT = 'month'
MONTH_FIRST_TRANSLATIONS = [
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y",
    "%I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%H:%M:%S",
]

DAY_FIRST_TRANSLATIONS = [
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y",
    "%I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S %p",
    "%d/%m/%Y %H:%M:%S",
    "%H:%M:%S",
]

def translate_date(date, date_hint=DEFAULT_DATE_HINT):
    if isinstance(date, basestring):
        if date_hint == 'month':
            date_translation_strings = MONTH_FIRST_TRANSLATIONS
        else:
            date_translation_strings = DAY_FIRST_TRANSLATIONS
        dt = None
        for format in date_translation_strings:
            try:
                dt = datetime.strptime(date, format)
                break
            except ValueError:
                pass

        if dt is None:
            raise ValueError("Could not convert %s to a proper date" % date)
        return dt
    else:
        if date.hour == date.minute == date.second == 0:
            return date.strftime("%m/%d/%Y")
        else:
            return date.strftime("%m/%d/%Y %I:%M:%S %p")

    raise TypeError("Expected either a string or datetime.datetime")

def map_attributes(attributes_map, attrs):
    for key, remap in attributes_map.iteritems():
        if key in attrs:
            attrs[remap] = attrs.pop(key)

def map_dates(dates, attrs, date_hint=DEFAULT_DATE_HINT):
    for key in attrs.iterkeys():
        if key in dates:
            attrs[key] = translate_date(attrs[key], date_hint)


VAMPIRE_TAG_RENAMES = {
    'startdate'    : 'start_date',
    'lastmodified' : 'last_modified',
    'id'           : 'id_text',
}
VAMPIRE_TAG_OVERRIDES = {
    'aurabonus'    : 'aura',
}
VAMPIRE_TAG_REMOVES = ('socialmax', 'mentalmax')
VAMPIRE_TAG_DEFAULTS = {
    'tempconscience' : 'conscience', 
    'tempselfcontrol': 'selfcontrol',
    'tempcourage'    : 'courage',
    'tempwillpower'  : 'willpower',
    'tempblood'      : 'blood',
    'temppathtraits' : 'pathtraits'
}
VAMPIRE_TAG_DATES = ('start_date', 'last_modified')

ENTRY_TAG_RENAMES = {'type':'change_type'}
ENTRY_TAG_DATES = ['date']

TRAIT_TAG_RENAMES = { 'val' : 'value' }

TRAITLIST_TAG_RENAMES = {
    'abc':'sorted',
    'display': 'display_preference',
}

def create_base_vampire(attrs, user, date_hint=DEFAULT_DATE_HINT):
    if not 'name' in attrs:
        raise RuntimeError("Can't create base vampire with no name in attrs")
    my_attrs = dict(attrs)
    map_attributes(VAMPIRE_TAG_RENAMES, my_attrs)
    map_dates(VAMPIRE_TAG_DATES, my_attrs, date_hint=date_hint)
    for key, value in VAMPIRE_TAG_OVERRIDES.iteritems():
        if key in my_attrs:
            my_attrs[value] = my_attrs[key]
            del my_attrs[key]
    for key in VAMPIRE_TAG_REMOVES:
        if key in my_attrs:
            del my_attrs[key]
    for key, value in VAMPIRE_TAG_DEFAULTS.iteritems():
        if key not in my_attrs:
            if value in my_attrs:
                my_attrs[key] = my_attrs[value]

    my_attrs['player'] = user
    my_attrs = dict([(str(k), v) for k,v in my_attrs.iteritems()])
    logging.info(pformat(my_attrs))
    vs = VampireSheet(name=my_attrs['name'])
    for key, value in my_attrs.iteritems():
        logging.info(pformat((key, value)))
        propertyDefinition = getattr(vs.__class__, key)
        if isinstance(propertyDefinition, IntegerProperty):
            setattr(vs, key, int(value))
        elif isinstance(propertyDefinition, BooleanProperty):
            asBool = True if value else False
            if value == "no":
                asBool = False
            setattr(vs, key, asBool)
        elif isinstance(propertyDefinition, FloatProperty):
            setattr(vs, key, float(value))
    vs.put()
    return vs

def read_experience_entry(attrs, current_vampire, previous_entry, date_hint=DEFAULT_DATE_HINT):
    my_attrs = dict(attrs)
    map_attributes(ENTRY_TAG_RENAMES, my_attrs)
    map_dates(ENTRY_TAG_DATES, my_attrs, date_hint)
    if previous_entry is not None:
        #print self.last_entry.date
        if previous_entry.date >= my_attrs['date']:
            #print my_attrs['date']
            my_attrs['date'] = previous_entry.date + timedelta(seconds=1)
    try:
        my_attrs = dict([(str(k), v) for k,v in my_attrs.iteritems()])
        return current_vampire.experience_entries.create(**my_attrs)
    except:
        pprint({'name':current_vampire.name, 'attrs':attrs, 'my_attrs':my_attrs})
        raise

def read_traitlist_properties(attrs, current_vampire):
    my_attrs = dict(attrs)
    map_attributes(TRAITLIST_TAG_RENAMES, my_attrs)
    my_attrs = dict([(str(k), v) for k,v in my_attrs.iteritems()])
    current_vampire.add_traitlist_properties(**my_attrs)

def read_trait(attrs, current_traitlist, current_vampire, order=None):
    my_attrs = dict(attrs)
    map_attributes(TRAIT_TAG_RENAMES, my_attrs)
    if 'value' in my_attrs:
        try:
            # TODO Remember this when handling the menu items
            # Some things have strange values like "2 or 4" and you need
            # to pick them before setting them in a sheet
            int(my_attrs['value'])
        except ValueError:
            my_attrs['value'] = 999999
    my_attrs['display_preference'] = current_traitlist['display']
    my_attrs = dict([(str(k), v) for k,v in my_attrs.iteritems()])
    if order is not None:
        my_attrs['order'] = order
    try:
        current_vampire.add_trait(current_traitlist['name'], my_attrs)
    except Exception, e:
        if e.args[0] == "columns sheet_id, traitlistname_id, name are not unique":
            # While we don't, Grapevine supports non-unique names in atomic traitlists
            # Just pass until we come up with a better way to report errors and
            # warnings in this code
            pass


