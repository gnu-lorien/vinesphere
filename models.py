# coding=utf-8
from datetime import datetime, timedelta
import logging
from pprint import pformat, pprint

import collections

from google.appengine.ext import db
from google.appengine.ext.db import polymodel

EXPERIENCE_ENTRY_CHANGE_TYPES = [
    (0, "Earn"),
    (1, "Lose"),
    (2, "Set Earned To"),
    (3, "Spend"),
    (4, "Unspend"),
    (5, "Set Unspent To"),
    (6, "Comment"),
]

class ExperienceEntry(db.Model):
    reason = db.TextProperty()
    change = db.FloatProperty()
    change_type = db.IntegerProperty()
    earned = db.FloatProperty()
    unspent = db.FloatProperty()
    date = db.DateTimeProperty(auto_now_add=True)

    class Meta:
        get_latest_by = "date"
        ordering = ["date"]
        verbose_name_plural = "experience entries"

    def __unicode__(self):
        return "<entry %s/>" % " ".join("%s=\"%s\"" % (fn, getattr(self, fn, '')) for fn in ExperienceEntry._meta.get_all_field_names() if fn not in ('id', 'sheet'))

DISPLAY_PREFERENCES = [
    (0, "name"),
    (1, "name xvalue (note)"),
    (2, "name xvalue dot (note)"),
    (3, "name dot (note)"),
    (4, "name (value, note)"),
    (5, "name (note)"),
    (6, "name (value)"),
    (7, "name (note)dotname (note)dot by value"),
    (8, "dot"),
    (9, "value"),
    (10,"note")
]

class Expendable(db.Model):
    name = db.StringProperty()
    value = db.IntegerProperty(default=1)
    modifier = db.IntegerProperty(default=0)
    dot_character = db.StringProperty(default='O')
    modifier_character = db.StringProperty(default='Õ')

class Sheet(polymodel.PolyModel):
    name = db.StringProperty(required=True)
    player = db.UserProperty()
    narrator = db.StringProperty()
    slug = db.StringProperty()
    home_chronicle = db.StringProperty()
    start_date = db.DateTimeProperty(auto_now_add=True)
    last_modified = db.DateTimeProperty(auto_now_add=True)

    npc = db.BooleanProperty(default=False)

    notes = db.TextProperty()
    biography = db.TextProperty()

    status = db.StringProperty()

    last_saved = db.DateTimeProperty(auto_now=True)

    experience_unspent = db.FloatProperty(default=0.0)
    experience_earned = db.FloatProperty(default=0.0)
    #experience_entries = db.ManyToManyProperty(ExperienceEntry)

    object_id = db.IntegerProperty()

    class Meta:
        unique_together = (("player", "name"))

    def __unicode__(self):
        return self.name

    def _get_slug(self):
        return " ".join([self.player.username, self.name])

    def get_traitlist(self, name):
        return self.traits.filter(traitlistname__name=name).order_by('order')

    def add_traitlist_properties(self, **kwargs):# name, sorted, atomic, negative, display_preference):
        """Updates the properties of a traitlist"""
        overwrite = kwargs.get('overwrite', True)
        def fix_bool_kwargs(key, kwargs):
            if key in kwargs:
                if isinstance(kwargs[key], basestring):
                    kwargs[key] = True if kwargs[key] == 'yes' else False
        for k in ['sorted', 'atomic', 'negative']:
            fix_bool_kwargs(k, kwargs)
        traitlist_name_obj, tl_created = TraitListName.objects.get_or_create(
                name=kwargs['name'],
                defaults={"slug":slugify(kwargs['name'])})
        del kwargs['name']
        if 'overwrite' in kwargs:
            del kwargs['overwrite']
        n_property, created = self.traitlistproperty_set.get_or_create(
                name=traitlist_name_obj,
                defaults=kwargs)
        if created is False and overwrite is True:
            changed = False
            for key, value in kwargs.iteritems():
                if getattr(n_property, key) != value:
                    changed = True
                    setattr(n_property, key, value)
            if changed:
                n_property.save()

    def get_traitlist_property(self, traitlistname):
        if isinstance(traitlistname, basestring):
            return self.traitlistproperty_set.get(name__name=traitlistname)
        else:
            return self.traitlistproperty_set.get(name=traitlistname)

    def get_traitlist_properties(self):
        return self.traitlistproperty_set.all()

    def get_traits(self, traitlist_name):
        traitlist_name_obj = TraitListName.objects.get(name=traitlist_name)
        return self.traits.filter(traitlistname=traitlist_name_obj)

    def _get_traitlist_name_obj(self, traitlist_name):
        k = 'traitlist_names:' + traitlist_name
        c = cache.get(k)
        if c is not None:
            return c
        try:
            traitlist_name_obj = TraitListName.objects.get(name=traitlist_name)
        except TraitListName.DoesNotExist:
            traitlist_name_obj = TraitListName.objects.create(name=traitlist_name, slug=slugify(traitlist_name))
        cache.set(k, traitlist_name_obj, 30)
        return traitlist_name_obj

    def add_trait(self, traitlist_name, trait_attrs):
        traitlist_name_obj = self._get_traitlist_name_obj(traitlist_name)
        if "order" not in trait_attrs:
            try:
                previous_last_order = self.traits.filter(traitlistname=traitlist_name_obj).only('order').values_list('order', flat=True).order_by('-order')[0]
                trait_attrs['order'] = previous_last_order + 1
            except IndexError:
                trait_attrs['order'] = 0
        self.traits.create(traitlistname=traitlist_name_obj, **trait_attrs)

    def insert_trait(self, traitlist_name, trait_attrs, order):
        for trait in self.traits.filter(order__gte=order):
            trait.order += 1
            trait.save()
        trait_attrs['order'] = order
        self.add_trait(traitlist_name, trait_attrs)

    def reorder_traits(self, traitlist_name, trait_names):
        """Reorders a traitlist to match the order given

        Should fail if the list doesn't exactly match having the traits named
        """
        tl = self.get_traitlist(traitlist_name)
        order_mapping = {}
        for i, tn in enumerate(trait_names):
            order_mapping[tn] = i
        for t in tl:
            if t.name not in trait_names:
                raise AttributeError("Reordering traitlist include the same members as the original traitlist. %s not found" % t.name)
            t.order = order_mapping[t.name]

        for t in tl:
            t.save()

    def _cascade_experience_expenditure_change(self, prev_entry, next_entry):
        #print "_cascade_experience_expenditure_change"
        if next_entry is None and prev_entry is None:
            #print "both none"
            # No entries left
            self.experience_unspent = self.experience_earned = 0
            self.save()
            return

        if next_entry is None:
            #print "next none"
            self.experience_unspent = prev_entry.unspent
            self.experience_earned = prev_entry.earned
            self.save()
            return

        try:
            #print "looping up"
            while True:
                self._calculate_earned_unspent_from_last(next_entry, prev_entry)
                next_entry.save()
                #print "next becomes", next_entry
                prev_entry = next_entry
                #print "prev is", next_entry
                next_entry = next_entry.get_next_by_date(sheet=self)
        except ExperienceEntry.DoesNotExist:
            #print "setting experience totals to", prev_entry
            self.experience_unspent = prev_entry.unspent
            self.experience_earned = prev_entry.earned
            self.save()
            return

        raise RuntimeError("Got to an invalid place cascading an experience entry")

    def delete_experience_entry(self, in_entry):
        entry = self.experience_entries.get(id=in_entry.id)
        try:
            prev_entry = entry.get_previous_by_date(sheet=self)
        except ExperienceEntry.DoesNotExist:
            # This means we're the first, so use the normal update method
            prev_entry = None
        try:
            next_entry = entry.get_next_by_date(sheet=self)
        except ExperienceEntry.DoesNotExist:
            # This means we're the last, so prev becomes the canonical view
            next_entry = None

        #print "Deleting entry", entry
        entry.delete()
        self._cascade_experience_expenditure_change(prev_entry, next_entry)

    def edit_experience_entry(self, in_entry):
        entry = self.experience_entries.get(id=in_entry.id)
        try:
            prev_entry = entry.get_previous_by_date(sheet=self)
        except ExperienceEntry.DoesNotExist:
            # This means we're the first, so use the normal update method
            prev_entry = None

        #print "Edited experience entry", entry
        #print "Prev experience entry", prev_entry
        self._cascade_experience_expenditure_change(prev_entry, entry)

    def add_experience_entry(self, entry):
        try:
            last_experience_entry = self.experience_entries.all().reverse()[0]
        except IndexError:
            last_experience_entry = None
        self._calculate_earned_unspent_from_last(entry, last_experience_entry)
        if last_experience_entry is not None:
            if last_experience_entry.date >= entry.date:
                entry.date = last_experience_entry.date + timedelta(seconds=1)
        entry.save()
        #print "(", entry.unspent, ",", entry.earned, ") ->", entry.change_type
        self.experience_entries.add(entry)
        self.experience_unspent = entry.unspent
        self.experience_earned = entry.earned
        self.save()

    def _calculate_earned_unspent_from_last(self, entry, previous_entry):
        if previous_entry is None:
            FauxEntry = collections.namedtuple('FauxEntry', 'unspent earned')
            previous_entry = FauxEntry(0, 0)
            #print "No last entry"
        entry.unspent = previous_entry.unspent
        entry.earned = previous_entry.earned
        #print entry.change_type, "->", entry.get_change_type_display()
        #print "previous_entry:", previous_entry
        if 3 == entry.change_type:
            entry.unspent = previous_entry.unspent - entry.change
        elif 0 == entry.change_type:
            entry.unspent = previous_entry.unspent + entry.change
            entry.earned = previous_entry.earned + entry.change
        elif 4 == entry.change_type:
            entry.unspent = previous_entry.unspent + entry.change
        elif 1 == entry.change_type:
            entry.earned = previous_entry.earned - entry.change
        elif 2 == entry.change_type:
            entry.earned = entry.change
        elif 5 == entry.change_type:
            entry.unspent = entry.change
        elif 6:
            pass

    def update_experience_total(self):
        try:
            entries = self.experience_entries.all().order_by('-date')
            self.experience_unspent = entries[0].unspent
            self.experience_earned = entries[0].earned
        except IndexError:
            self.experience_unspent = self.experience_earned = 0

    def add_default_traitlist_properties(self):
        s = self.get_specialization()
        if s == self:
            return
        s.add_default_traitlist_properties()

    def safe_delete(self):
        delete_storage_user = User.objects.get(username__startswith='deleted_character_sheets')
        self.player = delete_storage_user
        from datetime import datetime
        self.name = self.name + "||" + unicode(datetime.now())
        self.object_id = None
        self.content_type = None
        self.save()

    def get_specialization(self):
        try:
            return self.vampiresheet
        except:
            pass
        return self

    @staticmethod
    def filter_out_snapshots(qs):
        return qs.filter(am_i_a_snapshot__snapshot_sheet__name__isnull=True)

    @staticmethod
    def filter_only_snapshots(qs):
        return qs.filter(am_i_a_snapshot__snapshot_sheet__name__isnull=False)

    def snapshot(self):
        from copy import deepcopy
        self = self.get_specialization()
        copied_obj = deepcopy(self)

        from datetime import datetime
        copied_obj.name = copied_obj.name + "||snapshot" + unicode(datetime.now())
        copied_obj.pk = None
        copied_obj.id = None
        copied_obj.object_id = None
        copied_obj.content_type = None
        copied_obj.save()
        snapshot = Snapshot.objects.create(
            original_sheet = self,
            snapshot_sheet = copied_obj)

        for t in self.traits.all():
            t.sheet_id = copied_obj.id
            t.pk = None
            t.id = None
            t.save()
        for ee in self.experience_entries.all():
            add_ee = ExperienceEntry()
            add_ee.reason =      ee.reason
            add_ee.change =      ee.change
            add_ee.change_type = ee.change_type
            add_ee.earned =      ee.earned
            add_ee.unspent =     ee.unspent
            add_ee.date =        ee.date
            copied_obj.add_experience_entry(add_ee)
        return copied_obj

    def save(self, *args, **kwargs):
        self.slug = slugify(self._get_slug())
        super(Sheet, self).save(*args, **kwargs)

    def get_absolute_url(self, group=None):
        kwargs = {"sheet_slug": self.slug}
        # We check for attachment of a group. This way if the Task object
        # is not attached to the group the application continues to function.
        if group:
            return group.content_bridge.reverse("sheet_list", group, kwargs=kwargs)
        return reverse("sheet_list", kwargs=kwargs)

    def get_recent_expenditures_entry(self):
        entry = ExperienceEntry()
        recent_forward_date = self.last_modified
        try:
            last_entry_date = self.experience_entries.order_by('-date')[0].date
            if last_entry_date > recent_forward_date:
                recent_forward_date = last_entry_date
        except IndexError:
            pass
        changed_traits = Trait.history.filter(sheet_id=self.id)
        changed_traits = changed_traits.filter(history_date__gte=recent_forward_date)
        changed_traits = changed_traits.order_by('-history_date')

        logging.debug('get_recent_expenditures top %s' % pformat(changed_traits))
        final_reason_str = u''

        changed = []
        removed = []
        added = []
        matched_ids = set()
        for ct in changed_traits:
            if ct.id in matched_ids:
                continue
            matched_ids.add(ct.id)

            most_recent_historical_trait = ct
            try:
                older_historical_trait_to_compare = Trait.history.filter(
                    id=most_recent_historical_trait.id,
                    history_date__lt=recent_forward_date).order_by('-history_date')[0]
            except IndexError:
                older_historical_trait_to_compare = None

            if u'+' == ct.history_type:
                added.append({'most_recent_historical_trait': most_recent_historical_trait})
            elif u'~' == ct.history_type:
                if older_historical_trait_to_compare is None:
                    added.append({'most_recent_historical_trait': most_recent_historical_trait})
                else:
                    change_val = most_recent_historical_trait.value - older_historical_trait_to_compare.value
                    if 0 <= change_val:
                        changed.append({'most_recent_historical_trait': most_recent_historical_trait,
                                        'older_historical_trait_to_compare': older_historical_trait_to_compare})
                    elif 0 > change_val:
                        removed.append({'most_recent_historical_trait': most_recent_historical_trait,
                                        'older_historical_trait_to_compare': older_historical_trait_to_compare})
            elif u'-' == ct.history_type:
                if older_historical_trait_to_compare is not None:
                    removed.append({'most_recent_historical_trait': most_recent_historical_trait,
                                    'older_historical_trait_to_compare': older_historical_trait_to_compare})
            else:
                raise RuntimeError("Unknown history_type {}".format(ct.history_type))

        added.reverse()
        changed.reverse()
        removed.reverse()
        noted = []
        renamed = []

        entry.change = 0
        def get_trait_change_display(trait, display_val):
            value_u = u' x' + unicode(display_val) if display_val > 1 else u''
            trait_u = u' (' + trait.note + u')' if trait.show_note() else u''
            return trait.name + value_u + trait_u

        def get_real_trait_from_historical(t):
            try:
                return Trait.objects.get(id=t.id)
            except Trait.DoesNotExist:
                return Trait(name=t.name,
                             note=t.note,
                             value=t.value,
                             display_preference=t.display_preference)

        strs = []
        while len(added) > 0:
            t = get_real_trait_from_historical(added.pop(0)['most_recent_historical_trait'])
            change_val = t.value
            entry.change += change_val
            strs.append(get_trait_change_display(t, change_val))

        while len(changed) > 0:
            changed_row_dict = changed.pop(0)
            most_recent_historical_trait = changed_row_dict['most_recent_historical_trait']
            most_recent_trait = get_real_trait_from_historical(most_recent_historical_trait)
            older_historical_trait_to_compare = changed_row_dict['older_historical_trait_to_compare']
            non_val_differences = False
            if older_historical_trait_to_compare is None:
                change_val = most_recent_trait.value
            else:
                change_val = most_recent_trait.value - older_historical_trait_to_compare.value
                if most_recent_trait.note != older_historical_trait_to_compare.note:
                    noted.append((older_historical_trait_to_compare,
                                  get_real_trait_from_historical(most_recent_historical_trait)))
                    non_val_differences = True
                if most_recent_historical_trait.name != older_historical_trait_to_compare.name:
                    renamed.append((older_historical_trait_to_compare,
                                    get_real_trait_from_historical(most_recent_historical_trait)))
                    non_val_differences = True
            if non_val_differences and 0 == change_val:
                continue
            if 0 == change_val:
                continue
            entry.change += change_val
            strs.append(get_trait_change_display(most_recent_trait, change_val))
        if len(strs) > 0:
            final_reason_str += u'Purchased '
            final_reason_str += u', '.join(strs)
            final_reason_str += u'. '

        if len(removed) > 0:
            strs = []
            for removed_row_dict in removed:
                older_historical_trait_to_compare = removed_row_dict['older_historical_trait_to_compare']
                try:
                    most_recent_trait = Trait.objects.get(id=removed_row_dict['most_recent_historical_trait'].id)
                    change_val = most_recent_trait.value - older_historical_trait_to_compare.value
                    display_trait = most_recent_trait
                except Trait.DoesNotExist:
                    change_val = older_historical_trait_to_compare.value * -1
                    display_trait = older_historical_trait_to_compare
                entry.change += change_val
                strs.append(
                    get_trait_change_display(
                        get_real_trait_from_historical(display_trait),
                        abs(change_val)))
            final_reason_str += u'Removed '
            final_reason_str += u', '.join(strs)
            final_reason_str += u'. '

        if len(noted) > 0:
            strs = []
            for orig, new in noted:
                orig.display_preference = 1
                new.display_preference = 10
                strs.append(u'{orig.name} x{orig.value} ({orig.note}) to ({new})'.format(orig=orig, new=new))
            final_reason_str += u'Updated note '
            final_reason_str += u', '.join(strs)
            final_reason_str += u'. '

        if len(renamed) > 0:
            strs = []
            for orig, new in renamed:
                strs.append(u'{orig.name} x{orig.value} ({orig.note}) to {new.name} x{new.value} ({new.note})'.format(orig=orig, new=new))
            final_reason_str += u'Renamed '
            final_reason_str += u', '.join(strs)
            final_reason_str += u'. '

        if entry.change < 0:
            entry.change *= -1
            entry.change_type = 4
        elif entry.change > 0:
            entry.change_type = 3
        else:
            entry.change_type = 6
            
        entry.reason = final_reason_str.strip()
        from datetime import datetime
        entry.date = datetime.now()
        logging.debug('get_recent_expenditures bottom %s' % pformat(changed_traits))
        return entry

class VampireSheet(Sheet):
    nature = db.StringProperty()
    demeanor = db.StringProperty()
    blood = db.IntegerProperty(default=10)
    clan = db.StringProperty()
    conscience = db.IntegerProperty(default=3)
    courage = db.IntegerProperty(default=3)
    generation = db.IntegerProperty(default=13)
    path = db.StringProperty()
    pathtraits = db.IntegerProperty(default=3)
    physicalmax = db.IntegerProperty(default=10)
    sect = db.StringProperty()
    selfcontrol = db.IntegerProperty(default=2)
    willpower = db.IntegerProperty(default=2)
    title = db.StringProperty()

    aura = db.IntegerProperty(default=0)
    coterie = db.StringProperty()
    id_text = db.StringProperty()
    sire = db.StringProperty()

    # These need to actually default to whatever value was just set for their permanents
    # Or... better yet... get turned into something that doesn't blow since we have this
    # great uploading framework now!!!
    tempcourage = db.IntegerProperty(default=0)
    tempselfcontrol = db.IntegerProperty(default=0)
    tempwillpower = db.IntegerProperty(default=0)
    tempblood = db.IntegerProperty(default=0)
    tempconscience = db.IntegerProperty(default=0)
    temppathtraits = db.IntegerProperty(default=0)

    def add_default_traitlist_properties(self):
        self.add_traitlist_properties(overwrite=False, name="Physical", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Social", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Mental", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Negative Physical", sorted=True, negative=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Negative Social", sorted=True, negative=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Negative Mental", sorted=True, negative=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Status", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Abilities", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Influences", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Backgrounds", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Health Levels", sorted=False, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Bonds", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Miscellaneous", sorted=False, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Derangements", sorted=True, atomic=True, negative=True, display_preference=5)
        self.add_traitlist_properties(overwrite=False, name="Disciplines", sorted=False, atomic=True, display_preference=5)
        self.add_traitlist_properties(overwrite=False, name="Rituals", sorted=False, atomic=True, display_preference=5)
        self.add_traitlist_properties(overwrite=False, name="Merits", sorted=True, atomic=True, display_preference=4)
        self.add_traitlist_properties(overwrite=False, name="Flaws", sorted=True, atomic=True, negative=True, display_preference=4)
        self.add_traitlist_properties(overwrite=False, name="Equipment", sorted=True, display_preference=1)
        self.add_traitlist_properties(overwrite=False, name="Locations", sorted=True, atomic=True, display_preference=5)

#class TraitListProperty(db.Model):
#    sheet = db.ForeignKey(Sheet)
#    name = db.ForeignKey(TraitListName)
#    sorted = db.BooleanProperty(default=True)
#    atomic = db.BooleanProperty(default=False)
#    negative = db.BooleanProperty(default=False)
#    display_preference = db.SmallIntegerProperty(default=1, choices=DISPLAY_PREFERENCES)
#
#    class Meta:
#        ordering = ['name__name']
#        unique_together = (("sheet", "name"),)
#        verbose_name_plural = "trait list properties"
#
#    def __unicode__(self):
#        return "%s:%s" % (self.sheet, self.name)

class Formatter():
    def __init__(self,
            name, value, note,
            note_default,
            dot_character,
            display_preference):
        self.name               = name
        self.value              = value
        self.note               = note
        self.note_default       = note_default
        self.dot_character      = dot_character
        self.display_preference = display_preference

    def show_note(self):
        return self.note != Trait._meta.get_field_by_name('note')[0].get_default()
    def __show_val(self):
        return self.value >= 1
    def __tally_val(self):
        return self.__show_val()
    def tally_str(self):
        if self.value >= 1:
            return self.dot_character * self.value
        else:
            return ''

    def __unicode__(self):
        show_note = self.show_note()
        show_val  = self.__show_val()
        tally_val = self.__tally_val()

        if self.display_preference == 0:
            return self.name
        elif self.display_preference == 1:
            vstr = (" x%s" % (self.value)) if show_val else ''
            nstr = (" (%s)" % (self.note)) if show_note else ''
            return "%s%s%s" % (self.name, vstr, nstr)
        elif self.display_preference == 2:
            vstr = ''
            if show_val:
                vstr = (" x%s" % (self.value))
            if tally_val:
                vstr += " %s" % (self.tally_str())
            nstr = (" (%s)" % (self.note)) if show_note else ''
            return "%s%s%s" % (self.name, vstr, nstr)
        elif self.display_preference == 3:
            vstr = " %s" % (self.tally_str()) if tally_val else ''
            nstr = (" (%s)" % (self.note)) if show_note else ''
            return "%s%s%s" % (self.name, vstr, nstr)
        elif self.display_preference == 4:
            paren_str = ""
            if show_note and show_val:
                paren_str = " (%s, %s)" % (self.value, self.note)
            elif show_note and not show_val:
                paren_str = " (%s)" % (self.note)
            elif show_val and not show_note:
                paren_str = " (%s)" % (self.value)
            return "%s%s" % (self.name, paren_str)
        elif self.display_preference == 5:
            paren_str = ""
            if show_note:
                paren_str = " (%s)" % (self.note)
            return "%s%s" % (self.name, paren_str)
        elif self.display_preference == 6:
            paren_str = ""
            if show_val:
                paren_str = " (%s)" % (self.value)
            return "%s%s" % (self.name, paren_str)
        elif self.display_preference == 7:
            paren_str = (" (%s)" % (self.note)) if show_note else ''
            dstr = "%s%s" % (self.name, paren_str)
            its = []
            itrange = self.value if self.value >= 1 else 1
            for i in range(itrange):
                its.append(dstr)
            return self.dot_character.join(its)
        elif self.display_preference == 8:
            return self.tally_str()
        elif self.display_preference == 9:
            if show_val:
                return "%d" % (self.value)
            else:
                return ''
        elif self.display_preference == 10:
            if show_note:
                return self.note
            else:
                return ''

        return 'NOCING'

class Trait(db.Model):
    name = db.StringProperty()
    note = db.StringProperty()
    value = db.IntegerProperty(default=1)

    display_preference = db.IntegerProperty(default=1, choices=DISPLAY_PREFERENCES)
    dot_character = db.StringProperty(default='O')

    approved = db.BooleanProperty(default=False)

    order = db.IntegerProperty()
    sheet = db.ReferenceProperty(Sheet, collection_name="traits")
    #traitlistname = db.ForeignKey(TraitListName)

    class Meta:
        ordering = ['order']
        unique_together = (("sheet", "traitlistname", "name"),)

    def show_note(self):
        return self.note != Trait._meta.get_field_by_name('note')[0].get_default()
    def __show_val(self):
        return self.value >= 1
    def __tally_val(self):
        return self.__show_val()
    def tally_str(self):
        if self.value >= 1:
            return self.dot_character * self.value
        else:
            return ''

    def is_negative(self):
        tlp = TraitListProperty.objects.get(sheet=self.sheet, name=self.traitlistname)
        return tlp.negative

    def __unicode__(self):
        show_note = self.show_note()
        show_val  = self.__show_val()
        tally_val = self.__tally_val()

        if self.display_preference == 0:
            return self.name
        elif self.display_preference == 1:
            vstr = (" x%s" % (self.value)) if show_val else ''
            nstr = (" (%s)" % (self.note)) if show_note else ''
            return "%s%s%s" % (self.name, vstr, nstr)
        elif self.display_preference == 2:
            vstr = ''
            if show_val:
                vstr = (" x%s" % (self.value))
            if tally_val:
                vstr += " %s" % (self.tally_str())
            nstr = (" (%s)" % (self.note)) if show_note else ''
            return "%s%s%s" % (self.name, vstr, nstr)
        elif self.display_preference == 3:
            vstr = " %s" % (self.tally_str()) if tally_val else ''
            nstr = (" (%s)" % (self.note)) if show_note else ''
            return "%s%s%s" % (self.name, vstr, nstr)
        elif self.display_preference == 4:
            paren_str = ""
            if show_note and show_val:
                paren_str = " (%s, %s)" % (self.value, self.note)
            elif show_note and not show_val:
                paren_str = " (%s)" % (self.note)
            elif show_val and not show_note:
                paren_str = " (%s)" % (self.value)
            return "%s%s" % (self.name, paren_str)
        elif self.display_preference == 5:
            paren_str = ""
            if show_note:
                paren_str = " (%s)" % (self.note)
            return "%s%s" % (self.name, paren_str)
        elif self.display_preference == 6:
            paren_str = ""
            if show_val:
                paren_str = " (%s)" % (self.value)
            return "%s%s" % (self.name, paren_str)
        elif self.display_preference == 7:
            paren_str = (" (%s)" % (self.note)) if show_note else ''
            dstr = "%s%s" % (self.name, paren_str)
            its = []
            itrange = self.value if self.value >= 1 else 1
            for i in range(itrange):
                its.append(dstr)
            return self.dot_character.join(its)
        elif self.display_preference == 8:
            return self.tally_str()
        elif self.display_preference == 9:
            if show_val:
                return "%d" % (self.value)
            else:
                return ''
        elif self.display_preference == 10:
            if show_note:
                return self.note
            else:
                return ''

        return 'NOCING'

CREATURE_TYPES = [
    (0, "Mortal"),
    (1, "Player"),
    (2, "Vampire"),
    (3, "Werewolf"),
    (5, "Changeling"),
    (6, "Wraith"),
    (7, "Mage"),
    (8, "Fera"),
    (9, "Various"),
    (10, "Mummy"),
    (11, "Kuei-Jin"),
    (12, "Hunter"),
    (13, "Demon"),
]

CREATURE_TYPE_TO_NAME = dict(CREATURE_TYPES)
CREATURE_NAME_TO_TYPE = dict((p,l) for l,p in CREATURE_TYPES)

CREATURE_TYPE_SHEET_MAPPING = {
    VampireSheet: "Vampire",
}

class Menu(db.Model):
    name = db.StringProperty()
    category = db.IntegerProperty(choices=CREATURE_TYPES, default=1)
    sorted = db.BooleanProperty(default=False)
    negative = db.BooleanProperty(default=False)
    required = db.BooleanProperty(default=False)
    autonote = db.BooleanProperty(default=False)
    display_preference = db.IntegerProperty(default=0, choices=DISPLAY_PREFERENCES)

    def __unicode__(self):
        return pformat(self.__dict__)

    @classmethod
    def get_menu_for_traitlistname(self, traitlistname, sheet_class=None):
        translations = {
            'Negative Physical': 'Physical, Negative',
            'Negative Social': 'Social, Negative',
            'Negative Mental': 'Mental, Negative',
        }
        lookup_name = traitlistname.name
        if translations.has_key(lookup_name):
            lookup_name = translations[lookup_name]

        if sheet_class is not None:
            try:
                #from pprint import pprint
                #pprint(Menu.objects.filter(category=CREATURE_NAME_TO_TYPE[CREATURE_TYPE_SHEET_MAPPING[sheet_class]]).filter(name__startswith=lookup_name).values_list('name', flat=True))
                return Menu.objects.filter(category=CREATURE_NAME_TO_TYPE[CREATURE_TYPE_SHEET_MAPPING[sheet_class]]).get(name__startswith=lookup_name)
            except Menu.DoesNotExist:
                pass
            except Menu.MultipleObjectsReturned:
                pass
        return Menu.objects.get(name=lookup_name)

class MenuItem(db.Model):
    name = db.StringProperty()
    cost = db.StringProperty()
    note = db.StringProperty()
    order = db.IntegerProperty()
    menu_containing_this_item = db.ReferenceProperty(Menu)

    item_type = db.IntegerProperty(choices=[(0, "item"), (1, "include"), (2, "submenu")])
    menu_to_import = db.ReferenceProperty(Menu, collection_name='imported_menus')

    class Meta:
        ordering = ["order"]

    def __unicode__(self):
        formatter = Formatter(
            name=self.name,
            value=self.cost,
            note=self.note,
            note_default='',
            dot_character='O',
            display_preference=self.menu_containing_this_item.display_preference)
        return formatter.__unicode__()
