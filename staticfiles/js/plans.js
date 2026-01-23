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

    // Show toast notification
    function showToast(message, type = 'success') {
        // Create toast container if it doesn't exist
        let toastContainer = document.querySelector('.toast-container');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
            toastContainer.style.zIndex = '9999';
            document.body.appendChild(toastContainer);
        }

        // Create toast element
        const toastId = 'toast-' + Date.now();
        const bgClass = type === 'success' ? 'bg-success' : 'bg-danger';
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-white ${bgClass} border-0`;
        toast.setAttribute('role', 'alert');
        toast.setAttribute('aria-live', 'assertive');
        toast.setAttribute('aria-atomic', 'true');
        toast.id = toastId;
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        `;
        
        toastContainer.appendChild(toast);
        
        // Initialize and show toast
        const bsToast = new bootstrap.Toast(toast, { delay: 3000 });
        bsToast.show();
        
        // Remove toast after hidden
        toast.addEventListener('hidden.bs.toast', function() {
            toast.remove();
        });
    }

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
                    // Update icon only
                    if (data.is_saved) {
                        icon.classList.add('bi-heart-fill');
                        icon.classList.remove('bi-heart');
                        showToast('Saved to Favorites ❤️', 'success');
                    } else {
                        icon.classList.add('bi-heart');
                        icon.classList.remove('bi-heart-fill');
                        showToast('Removed from Favorites', 'success');
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
                    // Update icon only
                    if (data.in_comparison) {
                        icon.className = 'bi bi-check-square-fill';
                        showToast('Added to Compare ✓', 'success');
                    } else {
                        icon.className = 'bi bi-plus-square';
                        showToast('Removed from Compare', 'success');
                    }
                    
                    // Update navbar counter
                    updateNavbarCounters();
                    
                    // Show alert if max reached
                    if (data.message && data.message.includes('maximum')) {
                        showToast(data.message, 'danger');
                    }
                } else if (data.error) {
                    showToast(data.error, 'danger');
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
