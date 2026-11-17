"""#122: show an action's message in the event-page section the action returns to."""

from django import template

register = template.Library()

# Views tag their messages with one of these (messages.success(..., extra_tags=...)).
SECTIONS = ('attendance', 'discussion')


@register.filter
def in_section(messages, section):
    """The messages for `section`; 'page' means the untagged ones, shown at the top as usual."""

    def section_of(message):
        tags = set(message.extra_tags.split()) if message.extra_tags else set()
        return next((name for name in SECTIONS if name in tags), 'page')

    return [message for message in messages if section_of(message) == section]
