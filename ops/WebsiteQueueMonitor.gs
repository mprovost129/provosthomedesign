/** Independent website/queue monitor. Uses the existing owner's MailApp permission. */
function enableWebsiteQueueMonitor() {
  const handler = 'checkWebsiteQueueHealth';
  const existing = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === handler);
  if (!existing.length) ScriptApp.newTrigger(handler).timeBased().everyMinutes(5).create();
  console.log('Website queue monitoring enabled. Checks every five minutes; two failures trigger one owner alert.');
}

/** Setup diagnostics contain only public endpoint status, never client data. */
function verifyWebsiteQueueMonitor() {
  const endpoint = 'https://www.provosthomedesign.com/queue-health/';
  try {
    const response = UrlFetchApp.fetch(endpoint, {muteHttpExceptions: true, followRedirects: false});
    let ok = false;
    try { ok = JSON.parse(response.getContentText()).ok === true; } catch (_) {}
    console.log(JSON.stringify({httpStatus: response.getResponseCode(), healthy: ok}));
  } catch (error) {
    console.log(String(error).replace(endpoint, 'public health endpoint').slice(0, 250));
    throw new Error('Website monitor verification failed. Review its permission or connection error.');
  }
}

function checkWebsiteQueueHealth() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const properties = PropertiesService.getScriptProperties();
    const endpoint = 'https://www.provosthomedesign.com/queue-health/';
    let healthy = false;
    try {
      const response = UrlFetchApp.fetch(endpoint, {muteHttpExceptions: true, followRedirects: false});
      healthy = response.getResponseCode() === 200 && JSON.parse(response.getContentText()).ok === true;
    } catch (_) { /* Treat connection and malformed responses as failed checks. */ }
    const wasAlerted = properties.getProperty('WEBSITE_QUEUE_MONITOR_ALERTED') === 'yes';
    if (healthy) {
      properties.deleteProperty('WEBSITE_QUEUE_MONITOR_FAILURES');
      properties.deleteProperty('WEBSITE_QUEUE_MONITOR_ALERTED');
      if (wasAlerted) MailApp.sendEmail('mike@provosthomedesign.com', 'Provost work queue is healthy again',
        'The website and queue health checks have recovered.\n\nOpen your queue:\nhttps://www.provosthomedesign.com/work-queue/');
      return;
    }
    const failures = Number(properties.getProperty('WEBSITE_QUEUE_MONITOR_FAILURES') || 0) + 1;
    properties.setProperty('WEBSITE_QUEUE_MONITOR_FAILURES', String(failures));
    if (failures >= 2 && !wasAlerted) {
      // Mark before sending, so an ambiguous mail outcome never becomes repeated alerts.
      properties.setProperty('WEBSITE_QUEUE_MONITOR_ALERTED', 'yes');
      MailApp.sendEmail('mike@provosthomedesign.com', 'Provost work queue needs attention',
        'Two consecutive checks found a website or queue problem. This may be a stalled background job, delayed/failed email, calendar exception, or website outage.\n\n' +
        'Open the Work Queue and expand System health for details:\nhttps://www.provosthomedesign.com/work-queue/\n\n' +
        'The monitor runs independently of Render and contains no client names, documents, or credentials.');
    }
  } finally { lock.releaseLock(); }
}
