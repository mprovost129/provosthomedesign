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

    // Update navbar counters via AJAX instead of page reload
    function updateNavbarCounters() {
        // Update favorites count
        const favBadge = document.querySelector('.nav-link[href*="favorites"] .badge');
        if (favBadge) {
            fetch('/plans/favorites/', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(response => response.text())
            .then(html => {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const newCount = doc.querySelectorAll('.plan-card').length;
                favBadge.textContent = newCount;
                if (newCount > 0) {
                    favBadge.style.display = 'inline-block';
                } else {
                    favBadge.style.display = 'none';
                }
            })
            .catch(error => console.error('Error updating favorites count:', error));
        }
        
        // Update comparison count
        const compBadge = document.querySelector('.nav-link[href*="compare"] .badge');
        if (compBadge) {
            fetch('/plans/compare/', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(response => response.text())
            .then(html => {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const countText = doc.querySelector('p.text-muted')?.textContent || '';
                const match = countText.match(/Comparing (\d+)/);
                const newCount = match ? parseInt(match[1]) : 0;
                compBadge.textContent = newCount;
                if (newCount > 0) {
                    compBadge.style.display = 'inline-block';
                } else {
                    compBadge.style.display = 'none';
                }
            })
            .catch(error => console.error('Error updating comparison count:', error));
        }
    }
});
