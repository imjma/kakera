# Todoist iOS Shortcut

This Shortcut sends a shared URL directly to Todoist's cloud API. It does not
open Todoist, and the machine running Kakera can be off when you share the URL.

## Before you start

Create a Todoist project such as `Kakera Inbox`. You need its project ID and
your personal API token.

To copy the token in the Todoist web app, open **Settings → Integrations →
Developer → Copy API token**. See [Todoist's token instructions][token].

Use the same project ID configured as `todoist.project_id` in `kakera.json`. If
you still need it, list your projects and find the entry named `Kakera Inbox`:

```sh
curl https://api.todoist.com/api/v1/projects \
  -H "Authorization: Bearer YOUR_TODOIST_API_TOKEN" \
  | python3 -m json.tool
```

Copy that project's `id` value.

## Create the Shortcut

1. Open **Shortcuts** on the iPhone and tap **+**.
2. Name the shortcut `Send to Kakera`.
3. Open its details, enable **Show in Share Sheet**, and allow **URLs** and
   **Text** as input types. Text support covers apps that share a URL as text.
4. Add **Get URLs from Input** and set its input to **Shortcut Input**.
5. Add **Get Item from List** and select **First Item** from the URLs. Kakera
   needs one URL per Todoist task.
6. Add a **URL** action containing:

   ```text
   https://api.todoist.com/api/v1/tasks
   ```

7. Add **Get Contents of URL**, expand its options, and set **Method** to
   **POST**.
8. Add these headers:

   | Header | Value |
   | --- | --- |
   | `Authorization` | `Bearer YOUR_TODOIST_API_TOKEN` |
   | `Content-Type` | `application/json` |

   Include the space after `Bearer`.

9. Set **Request Body** to **JSON** and add these fields:

   | Key | Type | Value |
   | --- | --- | --- |
   | `content` | Text | The **First Item** magic variable from step 5 |
   | `project_id` | Text | Your Todoist project ID |

Todoist's [Create Task API][tasks] requires `content`; `project_id` sends the
task to the Kakera project instead of the default Inbox. Apple documents the
POST and JSON controls in [Get Contents of URL][shortcuts].

Do not add a Todoist action, **Open App**, **Quick Look**, or **Show
Notification**. The HTTP action is enough. Keep the Shortcut private because
anyone who can inspect a shared copy can read its API token.

## Test the workflow

1. Open a supported post in Safari or another app.
2. Tap **Share → Send to Kakera**.
3. On the first run, allow the Shortcut to contact `api.todoist.com`.
4. Wait for the small Shortcuts progress indicator to finish before locking
   the phone.
5. Confirm that a task containing the URL appears in `Kakera Inbox`.

Process it once on the Kakera machine:

```sh
export TODOIST_API_TOKEN="YOUR_TODOIST_API_TOKEN"
./kakera todoist
```

Or keep Kakera polling:

```sh
./kakera todoist --watch
```

After a successful capture, Kakera completes the task. Failed captures and
tasks without a URL remain open.

## Troubleshooting

- **401 Unauthorized:** check the token and the space after `Bearer`.
- **400 Bad Request:** make sure the body type is JSON and `content` receives
  the **First Item** magic variable.
- **Task goes to Todoist Inbox:** check the `project_id` field and ensure its
  type is Text.
- **Shortcut is missing from Share:** enable **Show in Share Sheet** and accept
  both URLs and Text.
- **Nothing happens after locking the phone:** run the Shortcut again and wait
  for its progress indicator to finish. This is an immediate, user-triggered
  request rather than unrestricted iOS background execution.

If the token is exposed, issue a new one in Todoist and update both the
Shortcut and `TODOIST_API_TOKEN` on the Kakera machine.

[shortcuts]: https://support.apple.com/en-au/guide/shortcuts/apd58d46713f/ios
[tasks]: https://developer.todoist.com/api/v1/#tag/Tasks/operation/create_task_api_v1_tasks_post
[token]: https://www.todoist.com/help/articles/find-your-api-token-Jpzx9IIlB
