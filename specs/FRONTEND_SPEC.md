# Spec: Frontend Demo UI

## Objective

Build a single-file demo UI at `static/index.html` that exercises every API endpoint implemented across the spec series. The UI uses vanilla HTML, CSS, and JavaScript — no framework, no build step, no npm, no bundler. The file is served directly by FastAPI's `StaticFiles` mount and is self-contained (no external JS or CSS dependencies).

---

## 1. Architecture

### Single-File Constraint

All HTML structure, CSS styles, and JavaScript logic live in one file: `static/index.html`. This keeps the demo portable — it can be opened directly in a browser pointing at the API without a dev server or build pipeline.

### FastAPI Static Mount

```python
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")
```

The UI is accessible at `http://localhost:8000/static/index.html`.

### API Communication

All API calls use the browser's native `fetch()` API with `async/await`. No third-party HTTP client is needed. Base URL is relative (same origin), so `fetch("/products")` works without configuration.

---

## 2. Layout Overview

```
┌────────────────────────────────────────────────────────┐
│  Stats Bar: Total Products | Avg Price | Avg Rating    │
├────────────────────────────────────────────────────────┤
│  Search Bar [input] + Suggestions Dropdown             │
├────────────────────────────────────────────────────────┤
│  Category Filter Buttons  [All] [Smartphones] [...]    │
├────────────────────────────────────────────────────────┤
│  Sort Buttons: [Price ↑] [Price ↓] [Rating] [Discount] │
│  Price Range: Min [___] Max [___]                      │
│  Quick: [Top Rated] [On Sale]                          │
├────────────────────────────────────────────────────────┤
│  Facet Pills (search mode): [Smartphones ×3] [...]     │
├────────────────────────────────────────────────────────┤
│  Product Grid  (responsive, 3–4 columns)               │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                  │
│  │thumb │ │thumb │ │thumb │ │thumb │                   │
│  │title │ │title │ │title │ │title │                   │
│  │price │ │price │ │price │ │price │                   │
│  └──────┘ └──────┘ └──────┘ └──────┘                  │
├────────────────────────────────────────────────────────┤
│  Pagination: [← Prev] Page 2 of 10 [Next →]           │
├────────────────────────────────────────────────────────┤
│  Live API Badge: GET /products?page=2 → 200 OK         │
└────────────────────────────────────────────────────────┘
```

---

## 3. Stats Bar

### Data Source

`GET /products/stats` — called once on page load.

### Displayed Fields

| Stat            | Value shown              |
|-----------------|--------------------------|
| Total Products  | `total_products`         |
| Avg Price       | `$avg_price` (2 decimal) |
| Avg Rating      | `avg_rating ★`           |

The stats bar is read-only and does not update dynamically when products are mutated (out of scope for a demo UI).

---

## 4. Category Filter Bar

### Data Source

`GET /categories` — called once on page load.

### Behaviour

- Renders one button per category plus an "All" button at the start.
- Clicking a category button sets the `category` filter and triggers a fresh `GET /products?category={slug}` call.
- The active category button is highlighted.
- Clicking "All" clears the category filter.

---

## 5. Sort Buttons

Renders four sort buttons:

| Button Label | `sort` parameter value |
|--------------|------------------------|
| Price ↑      | `price_asc`            |
| Price ↓      | `price_desc`           |
| Rating       | `rating_desc`          |
| Discount     | `discount_desc`        |

Clicking a sort button sets the active sort and reloads the product grid. Clicking the same button again clears the sort (toggles off). The active sort button is highlighted.

---

## 6. Price Range Inputs

Two number inputs: **Min Price** and **Max Price**. Calls `GET /products?min_price=&max_price=` when either value changes (with a 400ms debounce to avoid firing on every keystroke). Both are optional; the query parameter is omitted if the field is empty.

---

## 7. Top Rated and On Sale Quick Filters

Two buttons that switch the product grid into a special mode:

- **Top Rated** → calls `GET /products/top-rated?min_rating=4.5` and displays results.
- **On Sale** → calls `GET /products/on-sale?min_discount=10` and displays results.

While in Top Rated or On Sale mode, the sort/filter/pagination controls are hidden (these endpoints have their own ordering and do not support the standard `GET /products` parameters). Clicking either button again or clicking "All" returns to the standard browse mode.

---

## 8. Search Bar and Suggestions Dropdown

### Search Bar

Text input at the top of the page. On Enter key or after a 300ms debounce:
- Calls `GET /products?query={term}` (Elasticsearch full-text search).
- Displays results in the product grid with facet pills.

Clearing the search bar returns to browse mode.

### Suggestions Dropdown

While the user types (minimum 2 characters, 200ms debounce), a dropdown appears beneath the search bar populated by `GET /products/suggestions?query={term}`.

The suggestions dropdown includes:
- Up to 5 product title suggestions.
- A final "Search all results for '{term}'" entry at the bottom.

Clicking a suggestion:
- If it is a product title suggestion: fills the search bar with that title and triggers a search.
- If it is the "Search all" option: triggers `GET /search?query={term}` (global cross-resource search) and displays the grouped results page.

Pressing Escape closes the dropdown without selecting.

---

## 9. Product Grid

### Card Layout

Each product card displays:
- Thumbnail image (`thumbnail` field, with a placeholder if null).
- Product title (truncated to 2 lines with `text-overflow: ellipsis`).
- Price (bold, formatted as `$XX.XX`).
- Discount badge if `discount_percentage > 0` (e.g. `-15%` pill).
- Star rating display (filled/half/empty stars based on `rating`).
- Category slug as a small tag.

### Click Behaviour

Clicking a product card opens the **Product Detail Modal**.

---

## 10. Pagination

Displayed below the product grid for standard browse and search modes (not for Top Rated / On Sale modes).

Shows: **← Prev** | Page X of Y | **Next →**

- Prev/Next buttons call `GET /products?page={n}` with the current active filters.
- Current page and total pages are derived from the API response (`page`, `pages` fields).
- Prev is disabled on page 1; Next is disabled on the last page.

---

## 11. Product Detail Modal

### Trigger

Clicking a product card opens a modal overlay.

### Data Source

`GET /products/{id}` — fetches the full product detail (including `description`, all `images`, `stock`, `sku`, `weight`).

### Modal Contents

- Full title and description.
- Image gallery (thumbnail + additional images from `images` array, scrollable).
- Price, discount, rating, stock count.
- Brand, category, tags.
- A "Similar Products" section (see below).
- A close button (×) and click-outside-to-close behaviour.

### Similar Products Section

Within the modal, `GET /products/{id}/similar` is called concurrently with the product detail fetch. Returns up to 5 similar product cards displayed horizontally below the product details. Clicking a similar product replaces the modal content with that product's detail view.

---

## 12. Facet Pills on Search Results

When search results are displayed (i.e. `?query=` is active), a row of facet pills appears between the search bar and the product grid.

Each pill shows: `{Category Name} ({count})` — e.g. `Smartphones (4)`.

Clicking a facet pill adds a `category` filter to the current search query (calls `GET /products?query=phone&category=smartphones`). Active facet pills are highlighted. Clicking an active pill removes the filter.

**Data source:** The `facets.categories` array from the `GET /products?query=` response.

---

## 13. Live API Badge

A small fixed badge in the bottom-right corner of the page that shows the most recent API call made by the UI:

```
GET /products?query=phone&page=1   →   200 OK
```

Updates on every fetch call. Helps developers and demo viewers see exactly which endpoint was invoked. The HTTP status code is colour-coded: green for 2xx, yellow for 3xx/4xx, red for 5xx.

---

## 14. Global Search Results View

When the user selects "Search all" from the suggestions dropdown, the UI switches to a full global search results view using `GET /search?query={term}`.

This view renders two sections:

1. **Products** — a grid of matching products (same card layout as the main grid).
2. **Categories** — a horizontal list of matching category pills.

A "Back to browse" button returns to the normal grid view.

---

## Acceptance Criteria

- [ ] `static/index.html` is a single file with no external JS/CSS dependencies and no build step.
- [ ] Page loads without errors and the stats bar populates with data from `GET /products/stats`.
- [ ] Category filter buttons load from `GET /categories` and clicking one filters the product grid.
- [ ] Product grid paginates correctly; Prev/Next buttons work; page counter is accurate.
- [ ] Sort buttons trigger re-sorted product lists; the active sort is visually highlighted.
- [ ] Min/Max price inputs filter the product grid with debounce (no request on every keystroke).
- [ ] "Top Rated" button switches to `GET /products/top-rated` results; pagination is hidden.
- [ ] "On Sale" button switches to `GET /products/on-sale` results; pagination is hidden.
- [ ] Typing in the search bar shows a suggestions dropdown from `GET /products/suggestions`.
- [ ] Pressing Enter in the search bar triggers a full search via `GET /products?query=`.
- [ ] Search results include facet pills derived from `response.facets.categories`.
- [ ] Clicking a facet pill adds a category filter to the active search query.
- [ ] Clicking a product card opens the detail modal with full product info.
- [ ] The modal includes a "Similar Products" section populated from `GET /products/{id}/similar`.
- [ ] Clicking a similar product in the modal navigates to that product's detail view.
- [ ] Clicking "Search all" in the suggestions dropdown triggers `GET /search?query=` and shows grouped results.
- [ ] The live API badge updates on every fetch call, showing the endpoint URL and response status.
- [ ] The page is functional with the API running locally at `http://localhost:8000`.
- [ ] No console errors on initial page load with the API running.
