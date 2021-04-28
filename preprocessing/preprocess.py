import gc
import os
import sys
import ast
import glob
import json
import pickle
import argparse
import astunparse
import python_minifier
import numpy as np
import pandas as pd

from typing import *
from tqdm import tqdm
from collections import defaultdict
from yapf.yapflib.yapf_api import FormatCode
from pytransformation import Transformations
from pytransformation import String_Transformer
from sklearn.model_selection import train_test_split


def transform_method(code: str, transformations: List[Transformations]):
    """Returns the transformed source code

    Args:
        code (str): The entire method as a string
        transformations (List[Transformations]): A list of transformations to apply to the code
    """

    transformer = String_Transformer(transformations)
    transformed_source = transformer.transform(code)
    return transformed_source


class MethodExtractor:
    def __init__(self, args, transformations):
        self.args = args
        self.transformations = transformations
        self.classes = {key: idx for idx, key in enumerate(self.args.filters)}

    def filter_method_names(self, name):

        # Only keep method if it only matches one of the filter words and does not contain "test" as a substring
        matches = [x in name for x in self.args.filters]
        if "test" in name or "mock" in name:
            return None
        elif sum(matches) == 1:
            idx = np.where(matches)[0][0]
            return self.args.filters[idx]
        else:
            return '<RND>'

    def extract_methods_from_directory(self):
        files = glob.glob(f"{self.args.directory}/**/*.py", recursive=True)

        count, skipped, errors = 0, 0, 0
        with open(self.args.output_file, 'w') as writer:
            for fname in tqdm(files, desc="Extracting methods from directory"):
                # Open file and parse AST
                try:
                    with open(fname) as fh:
                        root = ast.parse(fh.read(), fname)
                except Exception as e:
                    if self.args.verbose:
                        print(
                            f"Skipping problematic file {e}", fname, file=sys.stderr)
                    skipped += 1
                    continue

                # Search for functions
                for node in ast.iter_child_nodes(root):
                    if isinstance(node, ast.FunctionDef):
                        label = self.filter_method_names(node.name)
                        if label is not None:
                            method_name = node.name

                            try:
                                node.name = "function"  # Hide method name
                                method_string = astunparse.unparse(node)
                                if len(method_string.split(' ')) > 1024:
                                    continue
                                augmented_method = transform_method(
                                    method_string, self.transformations)
                                augmented_method = python_minifier.minify(
                                    augmented_method, hoist_literals=False)
                                if len(augmented_method) == 0:
                                    continue
                                augmented_method, isValid = FormatCode(
                                    f"{augmented_method}", style_config='pep8', verify=True)

                                if isValid and method_string != augmented_method:
                                    # Write to original code and its augmentation to file
                                    writer.write(json.dumps(
                                        {"label_name": label, "method_name": method_name, "method": method_string, "augmentation": augmented_method}) + "\n")
                                    count += 1
                            except Exception as e:
                                errors += 1

                fh.close()
        print(
            f"Skipped {skipped} files. Extracted {count} methods. Number of methods discarded due to errors: {errors}")


def isSimilar(name, comparison):
    name_words = [x for x in name.split('_') if x]
    comparison_words = [x for x in comparison.split('_') if x]
    return any([c == n for n in name_words for c in comparison_words])


def index_triplets():
    for filename in ['./data/train.json', './data/val.json', './data/test.json']:
        df = pd.read_json(filename, lines=True)

        word2idx = defaultdict(list)
        for idx in tqdm(range(len(df)), desc="Indexing method names"):
            row = df.iloc[idx]
            subwords = [x for x in row.method_name.split('_') if x]
            for word in subwords:
                word2idx[word].append(idx)

        # Write indexation
        with open(filename.replace('json', 'pickle'), 'wb') as file:
            pickle.dump(word2idx, file, protocol=pickle.HIGHEST_PROTOCOL)


def create_datasets(filename):
    df = pd.read_json(filename, lines=True)
    df = df.sample(frac=1).reset_index(drop=True)  # shuffle

    # partition
    train_size, val_size, test_size = 0.95, 0.025, 0.025
    train, remainder = train_test_split(
        df, test_size=(1-train_size), shuffle=True)
    validate, test = train_test_split(
        remainder, test_size=test_size/(test_size + val_size))

    # save
    train.to_json('./data/train.json', orient='records', lines=True)
    validate.to_json('./data/val.json', orient='records', lines=True)
    test.to_json('./data/test.json', orient='records', lines=True)

    print(
        f"Created:\nTrain dataset with {len(train)} samples.\nValidation dataset with {len(validate)} samples.\nTest dataset with {len(test)} samples.\n")


def main():

    transformations = [
        Transformations.MAKE_FOR_LOOPS_WHILE,
        Transformations.MOVE_DECLS_TO_TOP_OF_SCOPE,
        Transformations.NAME_ALL_FUNCTION_APPLICATIONS,
    ]

    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('-d', '--directory', dest='directory', type=str, required=True,
                        help='The path to the directory to extract methods from.')
    parser.add_argument('-o', '--output_file', dest='output_file', type=str, required=False,
                        default="./data/methods.json", help='The path to the output file.')
    parser.add_argument('-v', '--verbose', dest='verbose', type=bool, required=False,
                        default=False, help='The verbosity of the logging.')
    parser.add_argument('-f', '--filters', dest='filters', default='train,save,process,forward,predict',
                        help='A comma separated list of method names to filter by.',
                        type=lambda s: [str(item) for item in s.split(',')])

    args = parser.parse_args()
    extractor = MethodExtractor(args, transformations)
    extractor.extract_methods_from_directory()
    create_datasets(args.output_file)
    index_triplets()


if __name__ == "__main__":
    main()