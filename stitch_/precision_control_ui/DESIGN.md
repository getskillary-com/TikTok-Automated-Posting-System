---
name: Precision Control UI
colors:
  surface: '#f9f9ff'
  surface-dim: '#d7dae3'
  surface-bright: '#f9f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f1f3fd'
  surface-container: '#ebeef7'
  surface-container-high: '#e5e8f1'
  surface-container-highest: '#dfe2ec'
  on-surface: '#181c22'
  on-surface-variant: '#404753'
  inverse-surface: '#2d3138'
  inverse-on-surface: '#eef0fa'
  outline: '#707785'
  outline-variant: '#c0c7d6'
  surface-tint: '#005fae'
  primary: '#005daa'
  on-primary: '#ffffff'
  primary-container: '#0075d5'
  on-primary-container: '#fefcff'
  inverse-primary: '#a5c8ff'
  secondary: '#4d6077'
  on-secondary: '#ffffff'
  secondary-container: '#cde1fd'
  on-secondary-container: '#51647c'
  tertiary: '#934600'
  on-tertiary: '#ffffff'
  tertiary-container: '#b95a00'
  on-tertiary-container: '#fffbff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d4e3ff'
  primary-fixed-dim: '#a5c8ff'
  on-primary-fixed: '#001c3a'
  on-primary-fixed-variant: '#004785'
  secondary-fixed: '#d1e4ff'
  secondary-fixed-dim: '#b4c8e3'
  on-secondary-fixed: '#071d31'
  on-secondary-fixed-variant: '#35485e'
  tertiary-fixed: '#ffdbc7'
  tertiary-fixed-dim: '#ffb688'
  on-tertiary-fixed: '#311300'
  on-tertiary-fixed-variant: '#733600'
  background: '#f9f9ff'
  on-background: '#181c22'
  surface-variant: '#dfe2ec'
typography:
  h1:
    fontFamily: PingFang SC, Microsoft YaHei, Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  h2:
    fontFamily: PingFang SC, Microsoft YaHei, Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
  body-base:
    fontFamily: PingFang SC, Microsoft YaHei, Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 22px
  body-sm:
    fontFamily: PingFang SC, Microsoft YaHei, Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 20px
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
  tabular-nums:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 22px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  sidebar_width: 240px
  header_height: 56px
  container_padding: 24px
  table_cell_padding_x: 12px
  table_cell_padding_y: 8px
  stack_gap_dense: 8px
  stack_gap_standard: 16px
---

## Brand & Style

The design system is engineered for high-stakes operational environments where data density and clarity are paramount. The brand personality is **authoritative, surgical, and utilitarian**, catering to system administrators who manage large-scale device clusters. 

The aesthetic follows a **Modern Corporate** style with a focus on structural integrity. It prioritizes "information over decoration," utilizing a strict grid and rigorous hierarchy to minimize cognitive load during complex distribution tasks. Every pixel is dedicated to functionality, ensuring that status changes across thousands of nodes are immediately perceivable.

## Colors

The color palette is strictly functional. The **Secondary Color (#001529)** is reserved for the fixed sidebar to create a strong structural anchor and reduce visual spill from the navigation into the data area. 

The **Primary Color** doubles as the 'Executing' state, creating a mental link between system action and interactive elements. Semantic colors follow a high-visibility logic:
- **Online/Success:** Vibrant green for "Go" states.
- **Executing/Processing:** Active blue for ongoing tasks.
- **Pending/Warning:** High-contrast yellow for attention.
- **Offline/Failed:** Urgent red for critical errors.
- **Archived/Disabled:** Low-impact gray to recede into the background.

The main content area utilizes a white surface against a light gray background to define container boundaries without heavy borders.

## Typography

This design system utilizes a "Dual-Script Precision" approach. For English characters and numerals, **Inter** is used for its superior legibility in small sizes and its tabular number support (essential for distribution counts and IP addresses). For Chinese characters, **PingFang SC** and **Microsoft YaHei** provide a clean, professional sans-serif appearance.

Type scales are kept tight. 14px is the standard for body and form inputs, while 12px is used for dense data tables and secondary labels. All numeric data in tables must use **tabular figures** to ensure columns of numbers align vertically for quick scanning.

## Layout & Spacing

The layout uses a **Fixed-Sidebar Fluid-Content** model. The sidebar is set to a constant width of 240px, while the main content area expands to fill the viewport, accommodating expansive data tables. 

A **4px base grid** governs all spacing. However, for this high-density system, "Compact" is the default state. Vertical padding in tables is restricted to 8px to maximize the number of visible rows on a single screen. Drawer components slide in from the right, occupying 40% of the screen width to allow users to maintain context of the main list while viewing details.

## Elevation & Depth

This design system avoids heavy shadows and skeuomorphism, favoring **Tonal Layers** and **Low-Contrast Outlines**. 

- **Level 0 (Background):** The base gray (#f0f2f5) acts as the canvas.
- **Level 1 (Surface):** White cards and table containers appear slightly elevated via a 1px neutral-3 border.
- **Level 2 (Overlays):** Modal dialogs and Drawers use a subtle ambient shadow (0px 4px 12px rgba(0,0,0,0.08)) to distinguish them from the main content.
- **Visual Dividers:** Use 1px solid lines (#f0f0f0) for row separation in tables and section breaks in forms, ensuring clear structural definition without adding "weight."

## Shapes

The shape language is **Soft (0.25rem)**. This slight rounding provides a professional, modern feel without sacrificing the efficient, "grid-locked" look required for high-density dashboards. 

- **Buttons & Inputs:** 4px radius.
- **Status Tags:** 2px radius (near-sharp) to differentiate them from interactive buttons.
- **Modals & Drawers:** 8px (rounded-lg) on top corners to soften the transition when they overlay the primary interface.
- **Checkboxes:** 2px radius to maintain a precise, technical appearance.

## Components

### Data Tables
Tables are the core of the distribution system. Use sticky headers and fixed first columns (for device IDs). Row hover states should use a very light blue (#e6f7ff) to assist line tracking.

### Status Tags
Small, non-interactive indicators using background tints and solid text.
- **Success:** Background (#f6ffed), Border (#b7eb8f), Text (#52c41a)
- **Error:** Background (#fff1f0), Border (#ffa39e), Text (#f5222d)

### Compact Forms
Vertical form labels are preferred for speed of entry. Use a 4px gap between label and input. Group related fields in cards with light gray header backgrounds.

### Step Wizards
Used for distribution workflows. The wizard should be placed at the top of the content area, showing the linear progression (e.g., 1. Select Nodes -> 2. Upload Package -> 3. Execution Strategy).

### Drawer Details
When a table row is clicked, a right-aligned drawer opens. It contains a high-density "Description" list (Key-Value pairs) for specific node metadata and real-time execution logs in a monospaced font block.