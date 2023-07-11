import json
import time

import numpy as np

from scipy.io import wavfile

from sic_framework.devices.common_naoqi.naoqi_text_to_speech import NaoqiTextToSpeechRequest

from sic_framework.services.dialogflow.dialogflow_service import DialogflowConf, \
    GetIntentRequest, RecognitionResult, QueryResult, Dialogflow




def on_dialog(message):
    if message.response:
        if message.response.recognition_result.is_final:
            print("Transcript:", message.response.recognition_result.transcript)



keyfile_json = json.load(open("sail-380610-0dea39e1a452.json"))
conf = DialogflowConf(keyfile_json=keyfile_json,
                      project_id='sail-380610',
                      sample_rate_hertz=16000, )


samplerate, data = wavfile.read('office_top_short.wav')

with open('audio.wav', 'rb') as fh:
    while fh.tell() != FILE_SIZE: # get the file-size from the os module
        AUDIO_FRAME = fh.read(CHUNK_SIZE)
        output.write(AUDIO_FRAME)

dialogflow = Dialogflow(ip='localhost', conf=conf)
dialogflow.register_callback(on_dialog)
dialogflow.connect(nao.mic)

nao.tts.request(NaoqiTextToSpeechRequest("Hello!"))

print(" -- Ready -- ")
x = np.random.randint(10000)

for i in range(25):
    print(" ----- Conversation turn", i)
    reply = dialogflow.request(GetIntentRequest(x))

    print(reply.intent)

    if reply.fulfillment_message:
        text = reply.fulfillment_message
        print("Reply:", )
        nao.tts.request(NaoqiTextToSpeechRequest(text))

    if reply.intent:
        print("Intent:", reply.intent)

        name = reply.response.query_result.intent.display_name
        if name == "get_up":
            print("TEST")
            # nao.motion.request(NaoPostureRequest("Stand"))
        if name == "sit_down":
            print("TEST2")
            # nao.motion.request(NaoRestRequest())

    # if reply.response.output_audio:
    #     print("reply.response.output_audio", reply.response.output_audio)
    #     audio = reply.response.output_audio
    #     song = AudioSegment.from_wav(io.BytesIO(audio))
    #     play(song)

time.sleep(100)