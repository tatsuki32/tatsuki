// Life OS 04: the secretary bot adds events to your default Google Calendar and asks what is on a day.
// Deploy as a web app: execute as yourself, access for anyone. Treat the /exec URL like a password.
// It can add events and read their names and times. It cannot delete them, and it never returns places or notes.
var ZONE = 'Asia/Ho_Chi_Minh';

function reply(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}

function list(d) {
  var from = new Date(d.from + 'T00:00:00+07:00');
  var to = new Date(new Date(d.to + 'T00:00:00+07:00').getTime() + 24 * 60 * 60 * 1000);
  var events = CalendarApp.getDefaultCalendar().getEvents(from, to).map(function (ev) {
    var f = function (t, p) { return Utilities.formatDate(t, ZONE, p); };
    return {
      title: ev.getTitle(),
      allDay: ev.isAllDayEvent(),
      date: f(ev.getStartTime(), 'yyyy-MM-dd'),
      start: f(ev.getStartTime(), 'HH:mm'),
      end: f(ev.getEndTime(), 'HH:mm')
    };
  });
  return reply({ ok: true, events: events });
}

function doPost(e) {
  var d = JSON.parse(e.postData.contents);
  if (d.action === 'list') return list(d);
  var cal = CalendarApp.getDefaultCalendar();
  var day = d.date.split('-');
  var from = new Date(Number(day[0]), Number(day[1]) - 1, Number(day[2]));
  var to = new Date(from.getTime() + 2 * 24 * 60 * 60 * 1000);
  var same = cal.getEvents(from, to).filter(function (ev) { return ev.getTag('ref') === d.ref; });
  if (same.length === 0) {
    var options = { description: d.note || '' };
    var ev;
    if (d.start) {
      var start = new Date(d.start);
      ev = cal.createEvent(d.title, start, new Date(start.getTime() + (d.minutes || 15) * 60 * 1000), options);
    } else {
      ev = cal.createAllDayEvent(d.title, from, options);
    }
    ev.setTag('ref', d.ref);
  }
  return reply({ ok: true });
}
