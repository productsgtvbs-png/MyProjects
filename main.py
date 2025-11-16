# main.py - Flask webhook for WhatsApp + Twilio Voice TwiML
from flask import Flask, request, jsonify, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
import os, requests, openai, gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

app = Flask(__name__)

# CONFIG: set these as env vars in Replit Secrets
TWILIO_ACCOUNT_SID = os.environ['TWILIO_ACCOUNT_SID']
TWILIO_AUTH_TOKEN = os.environ['TWILIO_AUTH_TOKEN']
TWILIO_WHATSAPP_FROM = os.environ['TWILIO_WHATSAPP_FROM']  # e.g. 'whatsapp:+1415XXXXXXX'
TWILIO_PHONE_NUMBER = os.environ['TWILIO_PHONE_NUMBER']    # Twilio voice number e.g. '+1XXX'
OPENAI_API_KEY = os.environ['OPENAI_API_KEY']
GOOGLE_SHEET_KEY = os.environ['GOOGLE_SHEET_KEY']  # spreadsheet key
SERVICE_ACCOUNT_JSON = os.environ['SERVICE_ACCOUNT_JSON']  # JSON contents

openai.api_key = OPENAI_API_KEY
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Google Sheets client
scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(eval(SERVICE_ACCOUNT_JSON), scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(GOOGLE_SHEET_KEY).sheet1

# Utility: append row to Google Sheets
def add_row(data):
    # data: list of column values
    sheet.append_row(data)

# Goggins tone prompt for GPT
COACH_PROMPT = """
You are Coach Grit-5 — extremely strict, like David Goggins. Short, direct, no excuses.
Rules:
- If user sends workout proof -> analyze and respond.
- If user sends diet photo -> analyze (food items, portion estimate) and respond strictly.
- If user sends screen-time screenshot -> call out high screen-time, give corrective tasks.
Always be firm, never rude or abusive.
Respond in English.
"""

@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    """Handle incoming WhatsApp messages (text or media) from Twilio."""
    from_number = request.form.get('From')  # e.g. 'whatsapp:+91XXXXXXXXXX'
    body = request.form.get('Body', '').strip()
    num_media = int(request.form.get('NumMedia', '0') or '0')
    resp = MessagingResponse()
    reply_text = ""

    if num_media > 0:
        # handle media: send to OpenAI vision -> caption/analysis
        media_url = request.form.get('MediaUrl0')
        media_content_type = request.form.get('MediaContentType0')

        # Send image to OpenAI Vision (example uses GPT-4o-mini/vision or gpt-4o with vision)
        # We'll use a simple image captioning approach: send URL and ask model to describe
        prompt = COACH_PROMPT + "\nAnalyze this image strictly: " + media_url + "\nAnswer concisely."
        try:
            ai_resp = openai.ChatCompletion.create(
                model="gpt-4o-mini",  # replace with the correct vision-enabled model from OpenAI
                messages=[
                    {"role":"system","content":COACH_PROMPT},
                    {"role":"user","content": "Analyze the image at: " + media_url + " Provide: 1) What it likely shows (workout/diet/screen), 2) any fitness observations, 3) one strict line telling the user what to do next."}
                ],
                max_tokens=300
            )
            analysis = ai_resp['choices'][0]['message']['content'].strip()
        except Exception as e:
            analysis = f"Could not analyze image automatically. Error: {str(e)}"

        # store result in Google Sheets
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        add_row([timestamp, from_number, "media", media_url, analysis])

        reply_text = analysis

    else:
        # handle text commands
        text = body.lower()
        if "skip" in text or "miss" in text:
            reply_text = "Skipped? Not acceptable. Explain WHY you skipped and text 'commit' to confirm you'll make it up."
        elif "commit" in text:
            reply_text = "Good. Make today count. Send workout proof after you finish."
        elif "status" in text:
            # simple status: show last 7 days consistency (basic)
            cells = sheet.get_all_records()
            # compute simple metric
            last_rows = cells[-10:] if len(cells) > 10 else cells
            workout_days = sum(1 for r in last_rows if r.get('type','').lower().startswith('work') or 'workout' in (r.get('type','').lower()))
            reply_text = f"You logged {workout_days} workout proofs in the last {len(last_rows)} entries. Be better."
        else:
            # pass to GPT for coaching text reply
            try:
                ai = openai.ChatCompletion.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role":"system","content":COACH_PROMPT},
                        {"role":"user","content": body}
                    ],
                    max_tokens=200
                )
                reply_text = ai['choices'][0]['message']['content'].strip()
            except Exception as e:
                reply_text = "I couldn't process that. Send a photo of your workout or type 'status'."

        # Store text message to sheet
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        add_row([timestamp, from_number, "text", body, reply_text])

    resp.message(reply_text)
    return str(resp)

@app.route("/voice", methods=['POST','GET'])
def voice_twiml():
    """Return TwiML to speak the morning script."""
    vr = VoiceResponse()
    # Use strict Goggins script
    script = ("Wake up. It's 5AM. This is your discipline calling. "
              "You said you want to change — now prove it. Stand up. Get water. Get moving. "
              "Send workout proof on WhatsApp within 1 hour. No delays. No excuses. Stay hard.")
    vr.say(script, voice='alice', language='en-US')
    # Optionally gather digits for confirmation
    with vr.gather(num_digits=1, action='/call-response', timeout=6) as g:
        g.say("If you are up, press 1. If you are not up, press 2.")
    return Response(str(vr), mimetype='text/xml')

@app.route("/call-response", methods=['POST'])
def call_response():
    digits = request.form.get('Digits')
    from_number = request.form.get('From')
    vr = VoiceResponse()
    if digits == '1':
        vr.say("Good. Proof on WhatsApp within one hour. Stay hard.")
    else:
        vr.say("You failed the first test. I'm calling again in 30 minutes.")
        # For the demo: schedule a follow-up call via Twilio REST (simple sleep not ideal in production)
        # Trigger follow-up call (example immediately for testing)
        # twilio_client.calls.create(twiml='<Response><Say>Follow up: Get up now.</Say></Response>', to=from_number, from_=TWILIO_PHONE_NUMBER)
    return Response(str(vr), mimetype='text/xml')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
