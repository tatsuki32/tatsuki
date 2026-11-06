// Life OS 04: adds an event to your default Google Calendar when the bot posts here.
// Deploy as a web app: execute as yourself, access for anyone. Treat the /exec URL like a password.
// It only adds events. It cannot read or delete them.
function doPost(e) {
  var d = JSON.parse(e.postData.contents);
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
      ev = cal.createEvent(d.title, start, new Date(start.getTime() + 60 * 60 * 1000), options);
    } else {
      ev = cal.createAllDayEvent(d.title, from, options);
    }
    ev.setTag('ref', d.ref);
  }
  return ContentService.createTextOutput(JSON.stringify({ ok: true })).setMimeType(ContentService.MimeType.JSON);
}
