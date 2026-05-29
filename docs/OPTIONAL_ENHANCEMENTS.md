# Quick Implementation Guide - Optional Enhancements

## 1️⃣ Add Favorite/Compare Buttons to Plan Cards

### File: templates/plans/plans.html
**Find the plan card section and add buttons:**

```django
{# Inside the plan card, after price/details, add: #}
<div class="card-footer bg-transparent border-0 pt-0">
    <div class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-danger flex-fill toggle-favorite" 
                data-plan-id="{{ plan.id }}"
                title="Save to favorites">
            <i class="bi bi-heart{% if plan.id in saved_plan_ids %}-fill{% endif %}"></i>
            <span class="d-none d-md-inline">Save</span>
        </button>
        <button class="btn btn-sm btn-outline-success flex-fill toggle-comparison" 
                data-plan-id="{{ plan.id }}"
                title="Add to comparison">
            <i class="bi bi-{% if plan.id in comparison_plan_ids %}check-square-fill{% else %}plus-square{% endif %}"></i>
            <span class="d-none d-md-inline">Compare</span>
        </button>
    </div>
</div>
```

**Also update the view (plans/views.py) to pass saved_plan_ids and comparison_plan_ids:**

```python
def plan_list(request, house_style_slug=None):
    # ... existing code ...
    
    # Add these imports at top if not present:
    from .session_utils import get_saved_plan_ids, get_comparison_plan_ids
    
    # Add to context before return:
    context = {
        'plans': plans,
        'house_styles': HouseStyle.objects.all(),
        'selected_style': selected_style,
        'saved_plan_ids': get_saved_plan_ids(request),
        'comparison_plan_ids': get_comparison_plan_ids(request),
    }
    return render(request, 'plans/plans.html', context)
```

---

## 2️⃣ Add Global JavaScript for AJAX Interactions

### File: static/js/plans.js (NEW FILE)

```javascript
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

    // Update navbar counters (optional, requires page reload to see)
    function updateNavbarCounters() {
        // For now, just log. To update without reload, you'd need to:
        // 1. Add endpoint to get current counts
        // 2. Fetch and update DOM elements
        console.log('Counters updated - refresh page to see changes');
    }
});
```

### Include in base.html
**Add before `</body>` tag:**

```django
<script src="{% static 'js/plans.js' %}"></script>
```

---

## 3️⃣ Add Recently Viewed Section to Homepage

### Step 1: Create Template Tag
**File: plans/templatetags/plans_extras.py**

Add this function:

```python
from django import template
from plans.models import Plans
from plans.session_utils import get_recently_viewed_ids

register = template.Library()

@register.simple_tag
def get_recently_viewed_plans(request):
    """Get recently viewed plans from session."""
    plan_ids = get_recently_viewed_ids(request)
    if not plan_ids:
        return []
    
    # Get plans maintaining order from session
    plans_dict = {plan.id: plan for plan in Plans.objects.filter(id__in=plan_ids).select_related('house_style')}
    return [plans_dict[plan_id] for plan_id in plan_ids if plan_id in plans_dict]
```

### Step 2: Update Homepage Template
**File: templates/pages/home.html**

Add this section (after hero or before footer):

```django
{% load plans_extras %}

{# Recently Viewed Plans Section #}
{% get_recently_viewed_plans request as recent_plans %}
{% if recent_plans %}
<section class="py-5 bg-light">
    <div class="container">
        <div class="row mb-4">
            <div class="col-12">
                <h2 class="text-center">
                    <i class="bi bi-clock-history"></i> Recently Viewed Plans
                </h2>
                <p class="text-center text-muted">Pick up where you left off</p>
            </div>
        </div>
        
        <div class="row g-4">
            {% for plan in recent_plans|slice:":5" %}
            <div class="col-md-4 col-lg-2">
                <div class="card h-100 shadow-sm">
                    <a href="{% url 'plans:plan_detail' plan.house_style.slug plan.slug %}">
                        {% if plan.main_image %}
                        <img src="{{ plan.main_image.url }}" 
                             class="card-img-top" 
                             alt="{{ plan.name }}"
                             style="height: 150px; object-fit: cover;">
                        {% else %}
                        <div class="bg-light d-flex align-items-center justify-content-center" 
                             style="height: 150px;">
                            <span class="text-muted">No image</span>
                        </div>
                        {% endif %}
                    </a>
                    
                    <div class="card-body p-2">
                        <h6 class="card-title mb-1">
                            <a href="{% url 'plans:plan_detail' plan.house_style.slug plan.slug %}" 
                               class="text-decoration-none text-dark">
                                {{ plan.name }}
                            </a>
                        </h6>
                        <p class="small text-muted mb-1">{{ plan.square_feet }} sq ft</p>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</section>
{% endif %}
```

---

## 4️⃣ Add Buttons to Plan Detail Page

### File: templates/plans/plan_detail.html

**Add prominent buttons near the top (after title/price):**

```django
<div class="row mb-4">
    <div class="col-12">
        <div class="d-flex gap-2 justify-content-center">
            <button class="btn btn-outline-danger toggle-favorite" 
                    data-plan-id="{{ plan.id }}">
                <i class="bi bi-heart{% if is_saved %}-fill{% endif %}"></i>
                {% if is_saved %}Saved to Favorites{% else %}Save to Favorites{% endif %}
            </button>
            
            <button class="btn btn-outline-success toggle-comparison" 
                    data-plan-id="{{ plan.id }}">
                <i class="bi bi-{% if is_in_comparison %}check-square-fill{% else %}plus-square{% endif %}"></i>
                {% if is_in_comparison %}Added to Compare{% else %}Add to Compare{% endif %}
            </button>
            
            <a href="{% url 'pages:get_started' %}?plan={{ plan.id }}" 
               class="btn btn-primary">
                <i class="bi bi-envelope"></i> Get Started with This Plan
            </a>
        </div>
    </div>
</div>
```

**Note:** The `is_saved` and `is_in_comparison` variables are already being passed in the view context!

---

## 5️⃣ Update Static Files

### After making JavaScript/CSS changes:

```bash
# Collect static files for production
./env/Scripts/python.exe manage.py collectstatic --noinput
```

---

## 🎨 BONUS: Add CSS Animations

### File: static/css/styles.css

Add smooth transitions:

```css
/* Favorite/Compare button animations */
.toggle-favorite, .toggle-comparison {
    transition: all 0.3s ease;
}

.toggle-favorite:hover {
    transform: scale(1.05);
}

.toggle-comparison:hover {
    transform: scale(1.05);
}

.toggle-favorite .bi-heart-fill {
    color: #dc3545;
    animation: heartbeat 0.3s ease;
}

@keyframes heartbeat {
    0% { transform: scale(1); }
    50% { transform: scale(1.2); }
    100% { transform: scale(1); }
}

/* Badge animations */
.navbar .badge {
    animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
    from { opacity: 0; transform: scale(0.8); }
    to { opacity: 1; transform: scale(1); }
}

/* Card hover effects */
.plan-card {
    transition: transform 0.3s ease, box-shadow 0.3s ease;
}

.plan-card:hover {
    transform: translateY(-5px);
    box-shadow: 0 0.5rem 1rem rgba(0,0,0,0.15) !important;
}
```

---

## ✅ IMPLEMENTATION ORDER:

1. **Start with plan cards** - Add favorite/compare buttons
2. **Add JavaScript** - Enable AJAX interactions
3. **Update plan_detail** - Add prominent buttons
4. **Add recently viewed** - Homepage section
5. **Add CSS animations** - Polish the UI
6. **Collect static files** - For production

---

## 🧪 TESTING AFTER EACH STEP:

- Test in browser (clear cache)
- Check console for errors (F12)
- Test on mobile (DevTools)
- Verify AJAX works (no page reload)
- Check navbar counters update

---

That's it! All optional enhancements are documented. Let me know which ones you'd like me to implement! 🚀
