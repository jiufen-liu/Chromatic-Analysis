# Hotfix58 · DG-2.2 · Permission UX / Find Scope

1. Replaces the old two-step / template-oriented administrator flow with a single integrated user-and-permission screen.
2. Removes role-template controls from the visible workflow.
3. Adds permission copy between ordinary users; identity/password/status are not copied.
4. Keeps feature permissions and data access permissions as independent gates whose effective result is their intersection.
5. Makes official/formal libraries visually consistent in the data permission tree.
6. Replaces Find's single customer selector with an authorised multi-select tree.
7. Find task scope is cached per task and changing it invalidates old results.
8. Denied resources are absent from the picker and remain blocked by LibraryStore SQL ACL.
