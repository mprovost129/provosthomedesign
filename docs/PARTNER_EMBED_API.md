# Partner Embed API

Allows partner websites to embed Provost Home Design plan cards or query the plan catalog.

---

## 1. Getting an API Key

1. Log into the Django admin at `/admin/`
2. Go to **Partner API Keys** and click **Add**
3. Enter the partner's site name and save — the key (`phd_xxxx...`) is auto-generated
4. Share the key with the partner
5. Optionally fill in **Allowed Origins** (e.g. `https://partnersite.com`) to restrict which domains the key works from

---

## 2. Embed Widget (easiest option)

No programming required. Partners add one `<div>` and one `<script>` tag anywhere on their page.

```html
<!-- Place where you want the plan card to appear -->
<div data-phd-plan="PLAN-NUMBER" data-phd-key="phd_YOUR_KEY_HERE"></div>

<!-- Include once, anywhere on the page -->
<script src="https://provosthomedesign.com/api/embed/widget.js"></script>
```

Replace `PLAN-NUMBER` with the plan's number (e.g. `PHD-1001`) and `phd_YOUR_KEY_HERE` with the API key.

**Multiple plans on one page** — repeat the `<div>` for each plan; only one `<script>` tag is needed.

```html
<div data-phd-plan="PHD-1001" data-phd-key="phd_YOUR_KEY_HERE"></div>
<div data-phd-plan="PHD-1042" data-phd-key="phd_YOUR_KEY_HERE"></div>
<script src="https://provosthomedesign.com/api/embed/widget.js"></script>
```

The rendered card includes: plan image, plan number, beds, baths, sq ft, stories, garage stalls, price (if set), and a "View Plan" button linking back to provosthomedesign.com.

---

## 3. JSON API (for developers)

Pass the API key via the `X-API-Key` header or `?api_key=` query parameter.

### List plans

```
GET https://provosthomedesign.com/api/plans/
X-API-Key: phd_YOUR_KEY_HERE
```

**Filters:**

| Parameter | Example | Description |
|---|---|---|
| `bedrooms` | `?bedrooms=3` | Exact bedroom count |
| `min_sqft` | `?min_sqft=1500` | Minimum square footage |
| `max_sqft` | `?max_sqft=3000` | Maximum square footage |
| `style` | `?style=ranch` | House style slug |
| `featured` | `?featured=true` | Featured plans only |
| `plan_number` | `?plan_number=PHD-1001` | Exact plan number |

### Single plan

```
GET https://provosthomedesign.com/api/plans/{plan_number}/
X-API-Key: phd_YOUR_KEY_HERE
```

### Example response

```json
{
  "plan_number": "PHD-1001",
  "slug": "phd-1001",
  "square_footage": 1850,
  "bedrooms": 3,
  "bathrooms": "2.5",
  "stories": 2,
  "garage_stalls": 2,
  "house_width_in": 480,
  "house_depth_in": 360,
  "house_width_display": "40′ 0″",
  "house_depth_display": "30′ 0″",
  "description": "...",
  "plan_price": "1299.00",
  "main_image_url": "https://provosthomedesign.com/media/plans/main/phd-1001.jpg",
  "gallery": [
    {
      "id": 1,
      "kind": "floor",
      "caption": "First floor plan",
      "order": 0,
      "image_url": "https://provosthomedesign.com/media/plans/gallery/phd-1001-floor.jpg"
    }
  ],
  "house_styles": [
    { "id": 1, "style_name": "Ranch", "slug": "ranch" }
  ],
  "is_featured": true,
  "url": "https://provosthomedesign.com/plans/ranch/phd-1001/"
}
```

---

## 4. Security Notes

- API keys are visible in client-side HTML source — this is expected (same model as Google Maps)
- Restrict keys to specific domains using the **Allowed Origins** field in the admin to prevent unauthorized use
- Keys can be deactivated instantly from the admin without contacting the partner
