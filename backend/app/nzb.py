import calendar
from xml.sax.saxutils import escape, quoteattr

HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">\n'
    '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
)


def build_nzb(rel) -> str:
    ts = calendar.timegm(rel.posted.timetuple())
    out = [HEAD]
    for fd in rel.files_data:
        subject = f'{rel.name} - "{fd["name"]}" yEnc (1/{fd["total"]})'
        out.append(
            f'<file poster={quoteattr(rel.poster)} date="{ts}" subject={quoteattr(subject)}>'
            f"<groups><group>{escape(rel.group_name)}</group></groups><segments>"
        )
        for n, msgid, nbytes in fd["parts"]:
            out.append(f'<segment bytes="{nbytes}" number="{n}">{escape(msgid.strip("<>"))}</segment>')
        out.append("</segments></file>\n")
    out.append("</nzb>\n")
    return "".join(out)
