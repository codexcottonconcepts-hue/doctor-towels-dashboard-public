# Application font

The application uses the existing embedded Satoshi family across dashboards, chart labels, forms, registers, reports, sign-in and account controls. Only `font-family` is overridden. Existing sizes, weights, line heights, colours and layout rules remain in place.

The shared dropdown assets carry the family override. Server pages embed the font faces there unless their head already contains them. Dashboard rendering reuses its existing head font faces and requests the override without a duplicate font set. The source font asset is unchanged and makes no external request.
