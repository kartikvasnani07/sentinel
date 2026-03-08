class StreamingPipeline:
    def __init__(self, llm_engine, tts_engine):
        self.llm = llm_engine
        self.tts = tts_engine
        self.stop_flag = False

    def stop(self):
        self.stop_flag = True
        self.tts.stop()

    def run(self, prompt, speak=False):
        self.stop_flag = False
        full_response = ""

        for token in self.llm.stream_generate(prompt):
            if self.stop_flag:
                break
            print(token, end="", flush=True)
            full_response += token

        print()

        if speak and full_response and not self.stop_flag:
            self.tts.speak(full_response, replace=True, interrupt=True)

        return full_response
