// ============================================================
// Drive File Sync — Auto-copy new files from a restricted shared
// folder into your own folder where you control sharing.
// ============================================================

// CONFIG — swap these with your actual folder IDs
const SOURCE_FOLDER_ID = 'YOUR_SOURCE_FOLDER_ID_HERE'; // the restricted shared folder
const DEST_FOLDER_ID   = 'YOUR_DEST_FOLDER_ID_HERE';   // your own folder

// How far back to look on first run (hours). Set higher for first run.
const INITIAL_LOOKBACK_HOURS = 24;

// ============================================================
// Main entry point — triggered by time-based trigger
// ============================================================
function syncNewFiles() {
  const props = PropertiesService.getScriptProperties();
  const lastSyncKey = 'LAST_SYNC_TIMESTAMP';
  
  let lastSync = props.getProperty(lastSyncKey);
  let lookbackDate;
  
  if (lastSync) {
    lookbackDate = new Date(parseInt(lastSync));
  } else {
    // First run — use the lookback window
    lookbackDate = new Date(Date.now() - INITIAL_LOOKBACK_HOURS * 60 * 60 * 1000);
  }
  
  const sourceFolder = DriveApp.getFolderById(SOURCE_FOLDER_ID);
  const destFolder   = DriveApp.getFolderById(DEST_FOLDER_ID);
  
  // Get files newer than our last sync time
  const files = sourceFolder.searchFiles(
    `modifiedDate > "${formatDateForQuery(lookbackDate)}"`
  );
  
  const now = Date.now();
  let copied = 0;
  
  while (files.hasNext()) {
    const file = files.next();
    
    // Check if we already processed this file
    if (props.getProperty('FILE_' + file.getId())) {
      continue; // already copied, skip
    }
    
    try {
      // Copy to destination (preserves type: Sheet stays Sheet, Doc stays Doc, etc.)
      file.makeCopy(file.getName(), destFolder);
      props.setProperty('FILE_' + file.getId(), String(now));
      copied++;
    } catch (e) {
      console.error('Failed to copy: ' + file.getName() + ' — ' + e.message);
    }
  }
  
  // Update sync timestamp
  props.setProperty(lastSyncKey, String(now));
  
  if (copied > 0) {
    console.log('Synced ' + copied + ' new file(s) at ' + new Date().toISOString());
  }
}

// ============================================================
// Helpers
// ============================================================
function formatDateForQuery(date) {
  // Google Drive search uses RFC 3339 format
  return Utilities.formatDate(date, Session.getScriptTimeZone(), "yyyy-MM-dd'T'HH:mm:ss");
}

// ============================================================
// One-time setup functions — run these manually once
// ============================================================

// Run this once to do an initial sync of all existing files
function initialSync() {
  const props = PropertiesService.getScriptProperties();
  // Clear the sync timestamp so it pulls everything
  props.deleteProperty('LAST_SYNC_TIMESTAMP');
  
  const sourceFolder = DriveApp.getFolderById(SOURCE_FOLDER_ID);
  const destFolder   = DriveApp.getFolderById(DEST_FOLDER_ID);
  const files = sourceFolder.getFiles();
  
  let copied = 0;
  while (files.hasNext()) {
    const file = files.next();
    try {
      file.makeCopy(file.getName(), destFolder);
      props.setProperty('FILE_' + file.getId(), String(Date.now()));
      copied++;
    } catch (e) {
      console.error('Failed: ' + file.getName());
    }
  }
  
  props.setProperty('LAST_SYNC_TIMESTAMP', String(Date.now()));
  console.log('Initial sync complete: ' + copied + ' files copied.');
}

// Run this once to set up the time trigger (daily at 6 AM)
function installTrigger() {
  // Remove any existing triggers for syncNewFiles
  const triggers = ScriptApp.getProjectTriggers();
  triggers.forEach(t => {
    if (t.getHandlerFunction() === 'syncNewFiles') {
      ScriptApp.deleteTrigger(t);
    }
  });
  
  // Create a new trigger — runs daily at 6 AM
  ScriptApp.newTrigger('syncNewFiles')
    .timeBased()
    .atHour(6)
    .everyDays(1)
    .create();
  
  console.log('Trigger installed. syncNewFiles will run daily at 6 AM.');
}

// Lists existing triggers — handy sanity check
function listTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  triggers.forEach(t => {
    console.log(t.getHandlerFunction() + ' — ' + t.getEventType() + ' — ' + t.getTriggerSource());
  });
}
