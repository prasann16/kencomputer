// Ask only for the OS microphone permission, only when recording is requested.
async function requestMicrophone(systemPreferences, platform) {
  if (platform !== 'darwin') return true;
  const status = systemPreferences.getMediaAccessStatus('microphone');
  if (status === 'granted') return true;
  if (status === 'not-determined') return systemPreferences.askForMediaAccess('microphone');
  return false;
}
module.exports = { requestMicrophone };
