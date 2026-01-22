// Global AJAX utilities for favorites and comparison

document.addEventListener('DOMContentLoaded', function() {
    // Get CSRF token from cookie
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    const csrftoken = getCookie('csrftoken');

    // Toggle Favorite
    document.querySelectorAll('.toggle-favorite').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const planId = this.dataset.planId;
            const icon = this.querySelector('i');
            
            fetch(`/plans/favorite/toggle/${planId}/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrftoken,
                    'Content-Type': 'application/json',
                },
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Update icon
                    if (data.is_saved) {
                        icon.classList.add('bi-heart-fill');
                        icon.classList.remove('bi-heart');
                    } else {
                        icon.classList.add('bi-heart');
                        icon.classList.remove('bi-heart-fill');
                    }
                    
                    // Update navbar counter
                    updateNavbarCounters();
                }
            })
            .catch(error => console.error('Error:', error));
        });
    });

    // Toggle Comparison
    document.querySelectorAll('.toggle-comparison').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const planId = this.dataset.planId;
            const icon = this.querySelector('i');
            
            fetch(`/plans/compare/toggle/${planId}/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrftoken,
                    'Content-Type': 'application/json',
                },
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Update icon
                    if (data.in_comparison) {
                        icon.className = 'bi bi-check-square-fill';
                    } else {
                        icon.className = 'bi bi-plus-square';
                    }
                    
                    // Update navbar counter
                    updateNavbarCounters();
                    
                    // Show alert if max reached
                    if (data.message && data.message.includes('maximum')) {
                        alert(data.message);
                    }
                } else if (data.error) {
                    alert(data.error);
                }
            })
            .catch(error => console.error('Error:', error));
        });
    });

    // Update navbar counters
    function updateNavbarCounters() {
        // Reload page to update counters (simple approach)
        // For a smoother experience, you could fetch counts via AJAX
        location.reload();
    }
});
