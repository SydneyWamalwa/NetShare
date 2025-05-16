// Update slider value for sharer dashboard
function updateSliderValue(val) {
  document.getElementById('limit_gb').value = val;
  document.querySelector('span.badge.bg-primary').textContent = val + ' GB';
}

// Handle connection status change for client
document.addEventListener('DOMContentLoaded', function() {
  const connectButton = document.querySelector('input[value="Connect to NetShare"]');
  const statusIndicator = document.querySelector('.status-indicator');
  const statusText = document.getElementById('status-text');
  const availableNetworks = document.getElementById('available-networks');
  
  if (connectButton) {
    connectButton.addEventListener('click', function() {
      // In a real app, this would be handled by the form submission
      // This is just for UI demonstration
      setTimeout(() => {
        if (statusIndicator.classList.contains('offline')) {
          statusIndicator.classList.remove('offline');
          statusIndicator.classList.add('online');
          statusText.textContent = 'Connected';
          connectButton.value = 'Disconnect';
          availableNetworks.style.display = 'block';
        } else {
          statusIndicator.classList.remove('online');
          statusIndicator.classList.add('offline');
          statusText.textContent = 'Not Connected';
          connectButton.value = 'Connect to NetShare';
          availableNetworks.style.display = 'none';
        }
      }, 1000);
    });
  }
});

// Flash message auto-dismiss
window.setTimeout(function() {
  const alerts = document.querySelectorAll('.alert-dismissible');
  alerts.forEach(function(alert) {
    if (alert) {
      const bsAlert = new bootstrap.Alert(alert);
      bsAlert.close();
    }
  });
}, 5000);
